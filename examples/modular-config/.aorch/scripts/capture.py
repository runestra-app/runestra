"""Capture a report summary as a structured workflow output."""

import json
import sys

context = json.load(sys.stdin)
message = context["step_inputs"]["message"]
print(json.dumps({"status": "succeeded", "summary": "Report summary captured", "outputs": {"summary": message}}))
