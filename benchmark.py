from pydantic import BaseModel
from PIL import Image
import traceback
import argparse
import ollama
import base64
import shutil
import json
import glob
import copy
import os
import csv

class CheckUnitA(BaseModel):
    choice: int

class CheckUnitB(BaseModel):
    choice: int
    why: str

def get_scan_areas(w: int, h: int, strength: int) -> list:
    areas = [
        (0, 0, w, h)
    ]
    if strength <= 0: return areas
    
    if strength <= 1:
        areas += [
            (0, 0, w // 2, h),
            (w // 2, 0, w // 2, h),
            (w // 4, 0, w // 2, h)
        ]
    elif strength <= 2:
        areas += [
            (0, 0, w // 2, h // 2),
            (w // 2, 0, w // 2, h // 2),
            (w // 4, 0, w // 2, h // 2),

            (0, h // 2, w // 2, h // 2),
            (w // 2, h // 2, w // 2, h // 2),
            (w // 4, h // 2, w // 2, h // 2),

            (0, h // 4, w // 2, h // 2),
            (w // 2, h // 4, w // 2, h // 2),
            (w // 4, h // 4, w // 2, h // 2)
        ]
    else:
        print("#error: strength only supports 0-2")

    return areas

def split_image(img_file: str, prefix: str, strength: int, cut_top: int) -> list:
    img = Image.open(img_file)

    wid, hei = img.size
    is_dual = wid > hei * 2 and wid > 1100

    if is_dual:
        half_wid = wid // 2

        dims = [
            (0, 0, half_wid, hei),
            (half_wid, 0, half_wid, hei)
        ]
    else:
        dims = [ (0, 0, wid, hei) ]

    sub_dims = []

    for x, y, w, h in dims:
        cy = cut_top if h > cut_top + 500 else 0

        for dx, dy, dw, dh in get_scan_areas(w, h - cy, strength):
            sub_dims.append([x + dx, y + cy + dy, dw, dh])
        
    clip_files, idx = [], 0

    for x, y, w, h in sub_dims:
        clip = img.crop((x, y, x + w, y + h))
        
        idx += 1
        dst_file = f"{prefix}-{idx}.png"

        clip_files.append(os.path.abspath(dst_file))
        clip.save(dst_file)        

    return clip_files

def check_clip(clip_file: str, pretrained: str, msg: list, check_unit: object, option: dict, rule_max: int) -> tuple:
    try:
        res = ollama.chat(pretrained, [msg], None, False, check_unit.model_json_schema(), option)

        answer = json.loads(res.get("message", {}).get("content"))
        dur = res.get("eval_duration", 0) / 1e6

        choice = answer.get("choice")
        if choice < 0 or choice >= rule_max: choice = 0

        why = answer.get("why", "")
        return choice, why, dur
    except Exception as exc:
        print(f"#error check_clip: {clip_file}")
        
        traceback.print_exc()
        exit(-1)

def check_image(image_file, idx, fab):
    prefix = "./temp/{:05}".format(idx)
    clip_files = split_image(image_file, prefix, fab["strength"], fab["cut"])
    
    ed, choice = 0, 0

    for clip in clip_files:
        msg = copy.deepcopy(fab["msg_template"])
        msg["images"] = [clip]

        choice, why, dur = check_clip(clip, fab["pretrained"], msg, CheckUnitB if fab["why"] else CheckUnitA, fab["option"], len(fab["nsfw_rule"]))
        ed += dur

        if fab["why"]:
            with open(fab["why_file"], "a", encoding = "utf-8") as fp:
                fp.write(f"{clip} >>> {choice} >>> {why}\n")

        if fab["verbose"]:
            print(f"\t- {clip}: {choice}")

            if choice != 0:
                print("\t- early stopped")
                break

    if not fab["keep"]:
        for clip in clip_files:
            os.remove(clip)

    return choice, ed

def find_image_files(directory):
    all_files = glob.glob(os.path.join(directory, "**"), recursive = True)    
    image_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.gif')
    image_files = []
    
    for f in all_files:
        if os.path.isdir(f): continue    
        if f.lower().endswith(image_extensions): image_files.append(f.replace('\\', '/'))
    
    return sorted(image_files)

def get_fab(args):
    with open(args.conf, "r", encoding = "utf-8") as fp:
        fab = json.loads(base64.b64decode(fp.read().encode("utf-8")).decode("utf-8"))

    fab["msg_template"]["content"] = fab["instruct"].format(
        '\n'.join(
            [
                fab["choice_phrase"].format(i + 1, condition, i + 1) for i, condition in enumerate(fab["nsfw_rule"])
            ]
        ),
        fab["output_format_1"] if args.keep else fab["output_format_0"]
    )

    fab["why_file"] = os.path.join(args.dst_dir, "why.txt")
    fab["keep"] = args.keep
    fab["why"] = args.why
    fab["strength"] = args.strength
    fab["cut"] = args.cut
    fab["verbose"] = args.verbose

    return fab

def main(args):
    os.makedirs(args.dst_dir, exist_ok = True)
    os.makedirs("./temp", exist_ok = True)

    safe_dir = os.path.join(args.dst_dir, "safe")
    nsfw_dir = os.path.join(args.dst_dir, "nsfw")

    os.makedirs(safe_dir, exist_ok = True)
    os.makedirs(nsfw_dir, exist_ok = True)

    fab = get_fab(args)

    if args.why:
        with open(fab["why_file"], "w", encoding = "utf-8") as fp:
            fp.write("")

    image_files = find_image_files(args.src_dir)
    results = []

    for idx, f in enumerate(image_files):
        rcode, dur = check_image(f, idx, fab)
        results.append((f, rcode, dur))

        print(f"* {f} >>> result = {rcode} (latency = {dur}ms)")
        
        copy_dir = safe_dir if rcode == 0 else nsfw_dir
        _, ext = os.path.splitext(f)

        shutil.copy(f, os.path.join(copy_dir, "{:05}.{}".format(idx, ext)))

    with open(os.path.join(args.dst_dir, f"result-{args.strength}-{args.cut}.csv"), mode = "w", newline = '') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['File', 'Result', 'Latency (ms)'])
        writer.writerows(results)

    print(f'* Completed to scan total {len(image_files)} image files.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'Check images for NSFW content.')

    parser.add_argument("src_dir", type = str, help = "Directory to search for image files")
    parser.add_argument("dst_dir", type = str, help = "Output directory path")
    parser.add_argument("-f", "--conf", type = str, default = "nsfw.conf", help = "Configuration file path")
    parser.add_argument("-s", "--strength", type = int, default = 2, help = "Strength (0-2)")
    parser.add_argument("-c", "--cut", type = int, default = 50, help = "Top cut in pixels")
    parser.add_argument("-k", "--keep", action = "store_true", help = "Keep clip images")
    parser.add_argument("-w", "--why", action = "store_true", help = "Explain why")
    parser.add_argument("-v", "--verbose", action = "store_true", help = "Verbose mode")
    
    args = parser.parse_args()
    main(args)
