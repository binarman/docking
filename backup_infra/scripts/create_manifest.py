#!/usr/bin/python3
import argparse
import hashlib
import sys
import os
import pickle
from pathlib import Path

CLEAR_EOL = "\033[K"  # ANSI escape: erase from cursor to end of line

def get_hash(filename):
    with open(filename, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def print_progress(current, total):
    # Rewrite a single line in place: 'processing {current} out of {total}'.
    if not sys.stdout.isatty():
        # Piped/redirected: print only every 100th update (and the last one).
        if current % 100 == 0 or current == total:
            print("processing {} out of {}".format(current, total))
        return
    # \r returns the cursor to the start of the line; CLEAR_EOL erases
    # leftover characters if the new text is shorter than the previous one.
    sys.stdout.write("\rprocessing {} out of {}{}".format(current, total, CLEAR_EOL))
    sys.stdout.flush()

def create_manifest(root_dir, progress_callback=None):
    # First pass: count every file we need to analyze.
    total_files = 0
    for _, _, files in os.walk(root_dir):
        total_files += len(files)

    database = {}
    processed = 0
    for root, _, files in os.walk(root_dir):
        for f in files:
            processed += 1
            full_path = os.path.join(root, f)
            relative_path = Path(full_path).relative_to(root_dir, walk_up=True)
            h = get_hash(full_path)
            if h in database:
                database[h] += [relative_path]
            else:
                database[h] = [relative_path]
            if progress_callback:
                progress_callback(processed, total_files)
    return database

def main():
    parser = argparse.ArgumentParser(
        description="Create a manifest database of file hashes for a directory tree.")
    parser.add_argument("repository_path", help="base path of the repository to scan")
    parser.add_argument("manifest_path", help="path of the manifest file to write")
    parser.add_argument("--verbose", action="store_true",
                        help="display progress and summary counts (default: quiet)")
    args = parser.parse_args()

    manifest = create_manifest(
        args.repository_path,
        progress_callback=print_progress if args.verbose else None,
    )
    if args.verbose:
        if sys.stdout.isatty():
            print()  # leave the in-place progress line behind, move to the next line
        total_files = 0
        for key in manifest:
            total_files += len(manifest[key])
        print("total file count: {}".format(total_files))
        print("unique file count: {}".format(len(manifest)))
    effective_manifest_path = args.manifest_path
    index = 0
    while os.path.isfile(effective_manifest_path):
        index += 1
        effective_manifest_path = args.manifest_path + "." + str(index)
    if index != 0 and args.verbose:
        print("file already exists, adding suffix to database name:", effective_manifest_path)
    with open(effective_manifest_path, 'wb') as f:
        pickle.dump(manifest, f)

if __name__=="__main__":
    main()