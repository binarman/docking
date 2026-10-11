#!/usr/bin/python3
import hashlib
import sys
import os
import pickle
from pathlib import Path

def get_hash(filename):
    with open(filename, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def help():
    print("Usage: file ./create_manifest.py <repository base path> <manifest file path>")

def create_manifest(root_dir):
    database = {}
    for root, _, files in os.walk(root_dir):
        for f in files:
            full_path = os.path.join(root, f)
            relative_path = Path(full_path).relative_to(root_dir, walk_up=True)
            h = get_hash(full_path)
            if h in database:
                database[h] += [relative_path]
            else:
                database[h] = [relative_path]
    return database

def main():
    if len(sys.argv) != 3:
        help()
        exit()

    repository_path = sys.argv[1]
    manifest_path = sys.argv[2]
    manifest = create_manifest(repository_path)
    total_files = 0
    for key in manifest:
        total_files += len(manifest[key])
    print("total file count: {}".format(total_files))
    print("unique file count: {}".format(len(manifest)))
    effective_manifest_path = manifest_path
    index = 0
    while os.path.isfile(effective_manifest_path):
        index += 1
        effective_manifest_path = manifest_path + "." + str(index)
    if index != 0:
        print("file already exists, adding suffix to database name:", effective_manifest_path)
    with open(effective_manifest_path, 'wb') as f:
        pickle.dump(manifest, f)

if __name__=="__main__":
    main()