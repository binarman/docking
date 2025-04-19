from huggingface_hub import snapshot_download
import sys

if len(sys.argv) != 2:
  print("wrong number of arguments, expect one argument - huggingface repo, like \"openai/clip-vit-large-patch14\"")
  exit(1)

repo_to_download = sys.argv[1]
snapshot_download(repo_id=repo_to_download)
