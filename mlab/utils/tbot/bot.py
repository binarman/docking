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
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes
import logging

from comfy_adapter import ComfyAdapter


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


async def poll_generation(reply_message, generation_id, comfy):
    previous_status = None
    while True:
        status = comfy.get_status(generation_id)
        logging.info(f"received generation {generation_id} status {status}")
        if status[0] == "queue" and previous_status != "queue":
            reply_message = await reply_message.edit_caption("Generation is queued")
            # TODO process errors
        if status[0] == "processing" and previous_status != "processing":
            reply_message = await reply_message.edit_caption("Generation is in progress")
            # TODO process errors
        if status[0] == "error":
            reply_message = await reply_message.edit_caption("Generation was cancelled or error happened")
            # TODO process errors
            return
        if status[0] == "invalid":
            reply_message = await reply_message.edit_caption("Server error")
            # TODO process errors
            return
        if status[0] == "success":
            img = comfy.download_output(status[1])
            input_media = InputMediaPhoto(img, caption="Here is the generated image")
            reply_message = await reply_message.edit_media(input_media)
            # TODO process errors
            return
        previous_status = status[0]
        await asyncio.sleep(2)


async def message_handler(update, context):
    config = context.bot_data["config"]
    admin_id = config["admin_id"]
    logging.info(f"received request from user {update.effective_user.id}")

    if update.effective_user.id != admin_id:
        logging.info(f"user is not authorized, skipping")
        return

    if not update.message.photo:
        logging.info("no image provided")
        await update.message.reply_text("no image attached, nothing to do")
        return

    prompt = update.message.caption
    
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    photo_contents = io.BytesIO()
    await photo_file.download_to_memory(photo_contents)
    logging.info("image downloaded from telegram server")

    input_name = str(update.update_id) + ".jpg"
    comfy = ComfyAdapter(config["base_comfy_url"], config["comfy_api_key"], config["pipeline_template_path"])
    comfy.upload_input(photo_contents.getbuffer(), input_name)
    logging.info("image uploaded to comfyui")
    random_seed = int(random.random()*100500)
    generation_id = comfy.request_generation(input_name, prompt, random_seed)
    logging.info(f"request \"{prompt}\" added with id {generation_id}")
    
    with open(config["placeholder_img"], "rb") as img_f:
        placeholder_img = img_f.read()
    reply_message = await update.message.reply_photo(placeholder_img, caption="Starting processing image")
    logging.info("Sent placeholder for generation to telegram")

    await poll_generation(reply_message, generation_id, comfy)


def main():
    parser = argparse.ArgumentParser()
    logging.basicConfig(level=logging.INFO)
    parser.add_argument("--config", default="bot.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    app = ApplicationBuilder()\
        .token(config["bot_token"])\
        .read_timeout(30)\
        .write_timeout(30)\
        .build()
    app.bot_data["config"] = config
    app.add_handler(MessageHandler(None, message_handler, block=False))
    print("starting telegram bot", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
