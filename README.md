# PENTRA — Smart Pension Transaction Monitoring System

Single-file hackathon prototype.

## Run

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`

## Demo

1. Select a pensioner.
2. Process ₹10,000 for `2026-10`.
3. Process ₹10,000 again for the same pensioner and cycle.
4. PENTRA detects the duplicate and places it in `HOLD_REVIEW`.
5. Use Release or Adjust in the review table.
6. Try the simulated verified death-event review.

This prototype uses dummy data and does not connect to real government/bank systems.
