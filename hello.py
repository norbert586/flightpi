#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import time
from PIL import Image, ImageDraw, ImageFont
from lib import LCD_2inch  # driver we copied in

def main():
    disp = LCD_2inch.LCD_2inch()
    disp.Init()
    disp.clear()

    img = Image.new("RGB", (disp.height, disp.width), "black")
    draw = ImageDraw.Draw(img)

    font = ImageFont.load_default()
    draw.text((10, 10), "Hello Otto!", fill="white", font=font)
    draw.text((10, 30), "NORBET'S FLIGHT PI!", fill="white", font=font)
    draw.text((10, 50), time.strftime("%H:%M:%S"), fill="white", font=font)

    # Match demo orientation — we can change later if needed
    img = img.rotate(180)

    disp.ShowImage(img)
    time.sleep(35)
    disp.module_exit()

if __name__ == "__main__":
    main()
