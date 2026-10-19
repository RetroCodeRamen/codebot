# Sourced by the run_*.sh drivers: record the model's digest, sample the CPU temperature every 60 s, unload the model at the end.
env_start() {   # OUT_DIR
  curl -s 127.0.0.1:11500/api/tags | python3 -c "import json,sys; print(json.dumps([m for m in json.load(sys.stdin)['models'] if m['name']=='codebot-model:latest'], indent=1))" > "$1/env.json"
  ( while true; do echo "$(date +%T) $(sensors 2>/dev/null | grep -m1 'Package id 0' | awk '{print $4}')" >> "$1/temps.log"; sleep 60; done ) &
  SAMPLER=$!
}
env_stop() {
  curl -s 127.0.0.1:11500/api/generate -d '{"model":"codebot-model","keep_alive":0}' > /dev/null; echo "unloaded $(date +%T)"
  kill $SAMPLER
}
