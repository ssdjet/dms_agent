# DMS Claims Pipeline

## Local results interface

Install the optional test tools and the web app with:

```powershell
python -m pip install -e ".[test]"
```

Start the interface on the local computer:

```powershell
python -m dms_pipeline.web
```

Then open `http://127.0.0.1:8000`. The server binds to loopback only by default;
do not expose it on a network. Upload `.xls` or `.xlsx` files up to 20 MB,
select the worksheet and header row, then choose the columns to check.

Reference lists and a maximum amount are optional. Supply only values from an
approved source; when omitted, those checks are reported as not evaluated. The
results show counts by rule and worksheet row number, but never include source
row values. The checks are technical validations, not insurance coverage
decisions.

Uploaded files are held in a short-lived temporary directory while a request is
processed and removed afterward. Do not treat this local demo as a hardened,
multi-user production service.
