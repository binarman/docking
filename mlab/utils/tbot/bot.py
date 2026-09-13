#!/usr/bin/env python3
import argparse
import asyncio
import random
import json
import io
import threading
import time
import functools

import yaml
import numpy as np
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes

from comfy_adapter import ComfyAdapter


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def poll_generation(update, generation_id, comfy, loop):
    while True:
        status = comfy.get_status(generation_id)
        print(f"polling {generation_id} with {status}")
        if status[0] == "error":
            asyncio.run_coroutine_threadsafe(update.message.reply_text("Generation was cancelled or error happened"), loop)
            return
        if status[0] == "invalid":
            asyncio.run_coroutine_threadsafe(update.message.reply_text("Server error"), loop)
            return
        if status[0] == "success":
            img = comfy.download_output(status[1])
            asyncio.run_coroutine_threadsafe(update.message.reply_photo(img, caption="Here is the generated image"), loop)
            return
        time.sleep(2)


async def message_handler(update, context):
    config = context.bot_data["config"]
    admin_id = config["admin_id"]
    print("received request from user", update.effective_user.id)

    if update.effective_user.id != admin_id:
        return

    if not update.message.photo:
        print("no image provided")
        await update.message.reply_text("no image attached, nothing to do")
        return

    prompt = update.message.caption
    
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    photo_contents = io.BytesIO()
    await photo_file.download_to_memory(photo_contents)

    input_name = str(update.update_id) + ".jpg"
    comfy = ComfyAdapter(config["base_comfy_url"], config["comfy_api_key"], config["pipeline_template_path"])
    comfy.upload_input(photo_contents.getbuffer(), input_name)
    random_seed = int(random.random()*100500)
    generation_id = comfy.request_generation(input_name, prompt, random_seed)
    print(f"request \"{prompt}\" added with id {generation_id}")

    loop = asyncio.get_running_loop()
    threading.Thread(target=poll_generation, args=(update, generation_id, comfy, loop), daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="bot.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    app = ApplicationBuilder().token(config["bot_token"]).build()
    app.bot_data["config"] = config
    app.add_handler(MessageHandler(None, message_handler))
    print("starting telegram bot")
    app.run_polling()


if __name__ == "__main__":
    main()
