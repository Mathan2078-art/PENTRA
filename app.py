from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sqlite3
from datetime import datetime

app = FastAPI(title="PENTRA - Smart Pension Transaction Monitoring System")
DB = "pentra.db"

def now():
    return datetime.now().isoformat(timespec="seconds")

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def setup():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS pensioners(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, ppo TEXT UNIQUE, pension REAL
    );
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pensioner_id INTEGER, amount REAL, cycle TEXT,
        reference TEXT, status TEXT, duplicate INTEGER, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS cases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pensioner_id INTEGER, type TEXT, reason TEXT,
        cycle TEXT, status TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT, action TEXT, details TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pensioner_id INTEGER, message TEXT, created_at TEXT
    );
    """)
    if c.execute("SELECT COUNT(*) FROM pensioners").fetchone()[0] == 0:
        c.executemany(
            "INSERT INTO pensioners(name,ppo,pension) VALUES(?,?,?)",
            [
                ("Ravi Kumar","PPO10001",10000),
                ("Lakshmi Devi","PPO10002",12500),
                ("Suresh Babu","PPO10003",8500)
            ])
    c.commit()
    c.close()

setup()

class Payment(BaseModel):
    pensioner_id: int
    amount: float
    cycle: str
    reference: str

class Action(BaseModel):
    action: str

class DeathEvent(BaseModel):
    pensioner_id: int
    source: str
    event_date: str

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML

@app.get("/api/dashboard")
def dashboard():
    c=conn()
    result={
        "pensioners":c.execute("SELECT COUNT(*) FROM pensioners").fetchone()[0],
        "payments":c.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
        "alerts":c.execute("SELECT COUNT(*) FROM cases WHERE status='HOLD_REVIEW'").fetchone()[0],
        "holds":c.execute("SELECT COUNT(*) FROM payments WHERE status='HOLD_REVIEW'").fetchone()[0],
        "death_reviews":c.execute("SELECT COUNT(*) FROM cases WHERE type='DEATH_EVENT'").fetchone()[0]
    }
    c.close()
    return result

@app.get("/api/pensioners")
def pensioners():
    c=conn()
    rows=[dict(x) for x in c.execute("SELECT * FROM pensioners")]
    c.close()
    return rows

@app.get("/api/payments")
def payments():
    c=conn()
    rows=[dict(x) for x in c.execute("""
        SELECT payments.*, pensioners.name, pensioners.ppo
        FROM payments JOIN pensioners ON pensioners.id=payments.pensioner_id
        ORDER BY payments.id DESC
    """)]
    c.close()
    return rows

@app.get("/api/cases")
def cases():
    c=conn()
    rows=[dict(x) for x in c.execute("""
        SELECT cases.*, pensioners.name, pensioners.ppo
        FROM cases JOIN pensioners ON pensioners.id=cases.pensioner_id
        ORDER BY cases.id DESC
    """)]
    c.close()
    return rows

@app.get("/api/audit")
def audit():
    c=conn()
    rows=[dict(x) for x in c.execute("SELECT * FROM audit ORDER BY id DESC")]
    c.close()
    return rows

@app.post("/api/payment")
def add_payment(p: Payment):
    c=conn()
    person=c.execute("SELECT * FROM pensioners WHERE id=?",(p.pensioner_id,)).fetchone()
    if not person:
        c.close()
        raise HTTPException(404,"Pensioner not found")

    old=c.execute("""
        SELECT * FROM payments
        WHERE pensioner_id=? AND cycle=?
    """,(p.pensioner_id,p.cycle)).fetchall()

    duplicate=any(abs(float(x["amount"])-p.amount)<0.01 for x in old)
    status="HOLD_REVIEW" if duplicate else "PROCESSED"

    cur=c.execute("""
        INSERT INTO payments
        (pensioner_id,amount,cycle,reference,status,duplicate,created_at)
        VALUES(?,?,?,?,?,?,?)
    """,(p.pensioner_id,p.amount,p.cycle,p.reference,status,int(duplicate),now()))
    payment_id=cur.lastrowid

    if duplicate:
        reason=f"Repeated payment of ₹{p.amount:.2f} detected in {p.cycle}"
        cur=c.execute("""
            INSERT INTO cases
            (pensioner_id,type,reason,cycle,status,created_at)
            VALUES(?,?,?,?,?,?)
        """,(p.pensioner_id,"DUPLICATE_PAYMENT",reason,p.cycle,"HOLD_REVIEW",now()))
        case_id=cur.lastrowid

        c.execute("""
            INSERT INTO notifications(pensioner_id,message,created_at)
            VALUES(?,?,?)
        """,(p.pensioner_id,
             f"Additional/repeated pension transaction detected. Reference PENTRA-{case_id:04d}.",
             now()))

        c.execute("""
            INSERT INTO audit(actor,action,details,created_at)
            VALUES(?,?,?,?)
        """,("SYSTEM","DUPLICATE_FLAGGED",
             f"Payment {payment_id}; Case PENTRA-{case_id:04d}",now()))
    else:
        c.execute("""
            INSERT INTO audit(actor,action,details,created_at)
            VALUES(?,?,?,?)
        """,("SYSTEM","PAYMENT_ACCEPTED",
             f"Payment {payment_id}; ₹{p.amount:.2f}; {p.cycle}",now()))

    c.commit()
    c.close()
    return {"duplicate":duplicate,"status":status}

@app.post("/api/case/{case_id}/action")
def action(case_id:int, a:Action):
    action=a.action.upper()
    if action not in ["RELEASE","ADJUST","REJECT"]:
        raise HTTPException(400,"Invalid action")

    status={"RELEASE":"RELEASED","ADJUST":"ADJUSTED","REJECT":"REJECTED"}[action]
    c=conn()
    case=c.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone()
    if not case:
        c.close()
        raise HTTPException(404,"Case not found")

    c.execute("UPDATE cases SET status=? WHERE id=?",(status,case_id))
    c.execute("""
        UPDATE payments SET status=?
        WHERE pensioner_id=? AND cycle=? AND status='HOLD_REVIEW'
    """,(status,case["pensioner_id"],case["cycle"]))

    c.execute("""
        INSERT INTO audit(actor,action,details,created_at)
        VALUES(?,?,?,?)
    """,("OFFICER",action,f"Case PENTRA-{case_id:04d}",now()))
    c.commit()
    c.close()
    return {"status":status}

@app.post("/api/death-event")
def death_event(e:DeathEvent):
    c=conn()
    if not c.execute("SELECT id FROM pensioners WHERE id=?",(e.pensioner_id,)).fetchone():
        c.close()
        raise HTTPException(404,"Pensioner not found")

    reason=f"Authorised-source review: {e.source}; event date {e.event_date}"
    cur=c.execute("""
        INSERT INTO cases
        (pensioner_id,type,reason,cycle,status,created_at)
        VALUES(?,?,?,?,?,?)
    """,(e.pensioner_id,"DEATH_EVENT",reason,e.event_date,"HOLD_REVIEW",now()))
    case_id=cur.lastrowid

    c.execute("""
        INSERT INTO audit(actor,action,details,created_at)
        VALUES(?,?,?,?)
    """,("SYSTEM","DEATH_EVENT_REVIEW",f"Case PENTRA-{case_id:04d}",now()))
    c.commit()
    c.close()
    return {"case":"PENTRA-%04d"%case_id,"status":"HOLD_REVIEW"}

HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PENTRA</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial;background:#f3f5f8;color:#17202a}
header{background:#101820;color:#fff;padding:24px 5%;display:flex;justify-content:space-between;align-items:center}
.logo{font-size:30px;font-weight:800}.sub{color:#b9c8d5;margin-top:5px}
main{width:90%;max-width:1250px;margin:25px auto}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:15px}
.card,section{background:white;border-radius:14px;padding:20px;box-shadow:0 3px 12px #00000012}
.card b{font-size:30px;display:block;margin-top:8px}
section{margin-top:20px}
h2{margin-top:0}
.form{display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:10px}
input,select,button{padding:12px;border-radius:8px;border:1px solid #ccd3da}
button{background:#1769aa;color:white;border:0;font-weight:bold;cursor:pointer}
button.alt{background:#59636d}
table{width:100%;border-collapse:collapse}
th,td{padding:11px;border-bottom:1px solid #e8ebee;text-align:left}
.badge{padding:5px 9px;border-radius:20px;font-size:12px;font-weight:bold}
.good{background:#dff5e5}.warn{background:#ffe9b0}.bad{background:#ffd9d9}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}.form{grid-template-columns:1fr}.grid .card:last-child{grid-column:span 2}}
</style>
</head>
<body>
<header>
<div><div class="logo">PENTRA</div><div class="sub">Smart Pension Transaction Monitoring System</div></div>
<div>PROTOTYPE • DUMMY DATA</div>
</header>

<main>
<div class="grid">
<div class="card">Pensioners<b id="n1">0</b></div>
<div class="card">Transactions<b id="n2">0</b></div>
<div class="card">Open Alerts<b id="n3">0</b></div>
<div class="card">Payment Holds<b id="n4">0</b></div>
<div class="card">Death Reviews<b id="n5">0</b></div>
</div>

<section>
<h2>💳 Process Pension Payment</h2>
<form class="form" id="pay">
<select id="person"></select>
<input id="amount" type="number" value="10000">
<input id="cycle" value="2026-10">
<input id="ref" placeholder="Transaction ID">
<button>Process</button>
</form>
<p>Demo: process the same amount for the same pensioner and month twice.</p>
</section>

<section>
<h2>📊 Transactions</h2>
<table><thead><tr><th>ID</th><th>Pensioner</th><th>Amount</th><th>Cycle</th><th>Status</th><th>Duplicate</th></tr></thead>
<tbody id="payments"></tbody></table>
</section>

<section>
<h2>🚨 Review Cases</h2>
<table><thead><tr><th>Case</th><th>Pensioner</th><th>Type</th><th>Reason</th><th>Status</th><th>Action</th></tr></thead>
<tbody id="cases"></tbody></table>
</section>

<section>
<h2>🕊️ Simulate Verified Death Event</h2>
<form class="form" id="death">
<select id="dperson"></select>
<input id="source" value="Authorised Registration Source">
<input id="date" value="2026-10-08">
<div></div><button>Create Review</button>
</form>
</section>

<section>
<h2>🧾 Audit Trail</h2>
<table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead>
<tbody id="audit"></tbody></table>
</section>
</main>

<script>
const $=x=>document.getElementById(x);
async function get(u){return (await fetch(u)).json()}
async function refresh(){
 let d=await get('/api/dashboard');
 $('n1').textContent=d.pensioners;$('n2').textContent=d.payments;
 $('n3').textContent=d.alerts;$('n4').textContent=d.holds;$('n5').textContent=d.death_reviews;

 let p=await get('/api/pensioners');
 let options=p.map(x=>`<option value="${x.id}">${x.name} — ${x.ppo} — ₹${x.pension}</option>`).join('');
 $('person').innerHTML=options;$('dperson').innerHTML=options;

 let ps=await get('/api/payments');
 $('payments').innerHTML=ps.map(x=>`<tr><td>${x.id}</td><td>${x.name}</td><td>₹${x.amount}</td><td>${x.cycle}</td><td><span class="badge ${x.status==='HOLD_REVIEW'?'warn':'good'}">${x.status}</span></td><td>${x.duplicate?'YES':'NO'}</td></tr>`).join('');

 let cs=await get('/api/cases');
 $('cases').innerHTML=cs.map(x=>`<tr><td>PENTRA-${String(x.id).padStart(4,'0')}</td><td>${x.name}</td><td>${x.type}</td><td>${x.reason}</td><td><span class="badge ${x.status==='HOLD_REVIEW'?'bad':'good'}">${x.status}</span></td><td>${x.status==='HOLD_REVIEW'?`<button onclick="act(${x.id},'RELEASE')">Release</button> <button class="alt" onclick="act(${x.id},'ADJUST')">Adjust</button>`:'Done'}</td></tr>`).join('');

 let a=await get('/api/audit');
 $('audit').innerHTML=a.map(x=>`<tr><td>${x.created_at}</td><td>${x.actor}</td><td>${x.action}</td><td>${x.details}</td></tr>`).join('');
}
$('pay').onsubmit=async e=>{
 e.preventDefault();
 let r=await fetch('/api/payment',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({pensioner_id:+$('person').value,amount:+$('amount').value,cycle:$('cycle').value,reference:$('ref').value||'DEMO-'+Date.now()})});
 let d=await r.json();
 alert(d.duplicate?'⚠️ DUPLICATE DETECTED — HOLD/REVIEW':'✅ PAYMENT ACCEPTED');
 refresh();
}
$('death').onsubmit=async e=>{
 e.preventDefault();
 await fetch('/api/death-event',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({pensioner_id:+$('dperson').value,source:$('source').value,event_date:$('date').value})});
 alert('🕊️ Death-event review case created');
 refresh();
}
async function act(id,a){
 await fetch('/api/case/'+id+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:a})});
 refresh();
}
refresh();
</script>
</body>
</html>
"""

# Run locally with:
# pip install fastapi uvicorn
# uvicorn app:app --reload
