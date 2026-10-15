"""Token accounting without a tokenizer endpoint (Ollama has none). chars-per-token is CALIBRATED against the real server
(`codebot bench --preflight` measures it on cold prompts and writes logs/tokenizer_ratio.<model>.json, one per model); until then a conservative default
is used so the context can never silently overflow the model's window."""
import json, os

DEFAULT_RATIO = 3.0        # code and JSON tokenize denser than prose; 3.0 errs on the side of counting too many tokens

DEFAULT_MODEL = "codebot-model"

def model_of(proj): return proj.get("CODEBOT_MODEL", DEFAULT_MODEL)

def ratio_files(proj):
    """Where the calibration lives: one file PER MODEL (another model tokenizes differently); the default model also keeps the original file name."""
    m = model_of(proj); safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in m)
    files = [os.path.join(proj.home, "logs", f"tokenizer_ratio.{safe}.json")]
    if m == DEFAULT_MODEL: files.append(os.path.join(proj.home, "logs", "tokenizer_ratio.json"))
    return files

def ratio(proj):
    for path in ratio_files(proj):
        try:
            with open(path) as f: r = float(json.load(f)["chars_per_token"])
            return max(1.5, min(r * 0.92, DEFAULT_RATIO + 1.0))      # 8% safety margin under the measurement
        except (OSError, ValueError, KeyError): continue
    return DEFAULT_RATIO

def est(proj, text): return int(len(text) / ratio(proj)) + 1
def est_messages(proj, messages): return sum(est(proj, m["content"]) + 4 for m in messages)     # +4: per-message template overhead

def num_ctx(proj): return proj.get("NUM_CTX", 32768, int)
