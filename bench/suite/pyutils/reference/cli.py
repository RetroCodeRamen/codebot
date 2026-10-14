import argparse
import json

from textutil import parse_kv, slugify

p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="cmd", required=True)
sub.add_parser("slug").add_argument("text")
sub.add_parser("kv").add_argument("line")
a = p.parse_args()
print(slugify(a.text) if a.cmd == "slug" else json.dumps(parse_kv(a.line)))
