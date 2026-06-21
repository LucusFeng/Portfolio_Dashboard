# Run The App From Terminal

This guide starts from a fresh Terminal window and is written for this project on your
Mac. It also handles the common Anaconda issue where `(base)` steals the `python` command.

## 1. Open Terminal And Go To The Project

```bash
cd /Users/lukefeng/Desktop/TRADING/Portfolio_Dashboard
```

Confirm you are in the right folder:

```bash
pwd
ls
```

You should see files like:

```text
README.md
app
docs
requirements.txt
tests
```

## 2. Leave Anaconda Base If It Is Active

If your prompt shows `(base)`, run:

```bash
conda deactivate
```

If `(base)` is still visible, run it one more time:

```bash
conda deactivate
```

It is okay if Terminal says there is no active conda environment.

## 3. Create Or Recreate The Virtual Environment

Use your installed Python 3.14:

```bash
rm -rf .venv
/usr/local/bin/python3.14 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Your prompt should now show:

```text
(.venv)
```

## 4. Confirm Python Is Coming From `.venv`

Run:

```bash
which python
python --version
```

You want something like:

```text
/Users/lukefeng/Desktop/TRADING/Portfolio_Dashboard/.venv/bin/python
Python 3.14.x
```

If you see Anaconda instead, run:

```bash
conda deactivate
source .venv/bin/activate
which python
```

Do not continue until `which python` points inside this project’s `.venv`.

## 5. Install Dependencies

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

Use `python -m pip` instead of plain `pip`; it guarantees you install packages into the
same Python environment that will run the app.

## 6. Run The Tests

```bash
python -m pytest -q
```

Expected result:

```text
8 passed
```

If you see `ModuleNotFoundError: No module named 'app'`, confirm this file exists:

```bash
ls pytest.ini
```

Then make sure you are running tests from the project root:

```bash
pwd
```

It should be:

```text
/Users/lukefeng/Desktop/TRADING/Portfolio_Dashboard
```

## 7. Create A Minimal `.env`

Create a local `.env` file:

```bash
touch .env
```

Open it in your editor and add:

```bash
DATABASE_PATH=data/portfolio.sqlite3
MANUAL_USDCAD_RATE=1.35
IBKR_GATEWAY_BASE_URL=https://localhost:5000/v1/api
```

You can add IBKR Flex credentials later. For a quick local test, the sample CIBC CSV is
enough.

## 8. Start The App

```bash
python -m uvicorn app.main:app --reload
```

Expected output includes:

```text
Uvicorn running on http://127.0.0.1:8000
Application startup complete.
```

Leave this Terminal window open while using the app.

## 9. Open The Dashboard

Open this URL in your browser:

```text
http://127.0.0.1:8000
```

You should see the dashboard.

## 10. Quick Test With Sample CIBC CSV

On the dashboard:

1. Find **Upload CIBC CSV**.
2. Choose:

```text
tests/fixtures/cibc_transactions.csv
```

3. Submit the upload.

The dashboard should refresh and show sample TFSA/CIBC data. Some prices may be missing
because the sample CIBC rows do not have IBKR `conid` values for price lookup yet.

## 11. Stop The App

Go back to the Terminal window running Uvicorn and press:

```text
CTRL+C
```

## 12. Run It Again Later

Next time, you do not need to recreate `.venv` unless something breaks. Use:

```bash
cd /Users/lukefeng/Desktop/TRADING/Portfolio_Dashboard
conda deactivate
source .venv/bin/activate
which python
python -m pytest -q
python -m uvicorn app.main:app --reload
```

## Common Problems

### `python` points to Anaconda

Problem:

```text
/Users/lukefeng/opt/anaconda3/bin/python
```

Fix:

```bash
conda deactivate
source .venv/bin/activate
which python
```

### `python3.12: command not found`

You do not need Python 3.12. Use Python 3.14:

```bash
/usr/local/bin/python3.14 -m venv .venv
```

### Port 8000 Is Already In Use

Run on another port:

```bash
python -m uvicorn app.main:app --reload --port 8001
```

Then open:

```text
http://127.0.0.1:8001
```

### Reset The Local Dev Database

This deletes local app data and lets SQLite rebuild cleanly:

```bash
rm -f data/portfolio.sqlite3
python -m uvicorn app.main:app --reload
```
