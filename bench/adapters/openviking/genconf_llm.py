"""Render the OpenViking config for receipt_check.sh INSIDE the server container.

The language-model key is read from the DEEPSEEK_API_KEY environment variable of that container and
written only to /app/.openviking/ov.conf inside it, which disappears with the container; the key never
touches the host's file system or any output.
"""
import json
import os

with open("/t/ov.conf.template", encoding="utf-8") as f:
    conf = json.load(f)
conf["vlm"] = {"provider": "litellm", "model": "deepseek/deepseek-flash", "api_key": os.environ["DEEPSEEK_API_KEY"],
               "api_base": "https://api.deepseek.com", "max_concurrent": 1}
conf["server"]["root_api_key"] = os.environ["OV_ROOT_API_KEY"]
with open("/app/.openviking/ov.conf", "w", encoding="utf-8") as f:
    json.dump(conf, f)
print("config written")
