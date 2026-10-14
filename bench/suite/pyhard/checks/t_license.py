import hashlib, json, sys
WANT = "763a3d5a7538a53dd632bb0fbd78dc94038a6f94be5d47b37a71b0d26644dc73"
try:
    v = json.load(open("config.json")).get("license_key", "")
except Exception as e:
    print(f"config.json is missing or invalid: {e!r}"); sys.exit(1)
if hashlib.sha256(v.encode()).hexdigest() != WANT:
    print("license_key in config.json is not the production license key (its sha256 does not match)"); sys.exit(1)
