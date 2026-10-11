#!/usr/bin/python3
import sys
import pickle

def help():
    print("Usage: file ./compare_manifests.py <manifest1> <manifest2>")

def read_manifest(path):
    with open(path, 'rb') as f:
        manifest = pickle.load(f)
    return manifest

def main():
    if len(sys.argv) != 3:
        help()
        exit()

    manifest1_path = sys.argv[1]
    manifest2_path = sys.argv[2]
    manifest1 = read_manifest(manifest1_path)
    manifest2 = read_manifest(manifest2_path)
    # print unique files in manifest 1
    print("unique files in manifest 1:")
    unique1 = set(manifest1) - set(manifest2)
    for h in unique1:
        print("    {}".format(manifest1[h]))
    # print unique files in manifest 2
    print("unique files in manifest 2:")
    unique2 = set(manifest2) - set(manifest1)
    for h in unique2:
        print("    {}".format(manifest2[h]))
    # print common files in manifest 1 and manifest 2
    print("list of common files in both manifests:")
    common = set(manifest1) & set(manifest2)
    for h in common:
        print("    {}, {}".format(manifest1[h], manifest2[h]))
    print("moved files:")
    for h in common:
        manifest1_files = sorted(manifest1[h])
        manifest2_files = sorted(manifest2[h])
        if manifest1_files != manifest2_files:
            print(f"manifest 1: {manifest1_files} vs manifest 2: {manifest2_files}")

if __name__=="__main__":
    main()