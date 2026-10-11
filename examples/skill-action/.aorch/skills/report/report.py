import json
import sys

context = json.load(sys.stdin)
text = context["step_inputs"]["text"]
print(json.dumps({"status": "succeeded", "summary": "Document word count prepared", "outputs": {"word_count": len(text.split())}}))
