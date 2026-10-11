"""Return the review summary as a checked workflow output."""

import json
import sys

context = json.load(sys.stdin)
message = context["step_inputs"]["message"]
print(json.dumps({"status": "succeeded", "summary": "Review summary accepted", "outputs": {"summary": message}}))
