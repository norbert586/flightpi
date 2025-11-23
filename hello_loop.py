#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import time
from PIL import Image, ImageDraw, ImageFont
from lib import LCD_2inch

# Colors for Michigan theme
BLUE = (0, 48, 135)    # True U-M Blue
MAIZE = (255, 203, 5)  # True U-M Maize

def draw_once(disp):
    img = Image.new("RGB", (disp.height, disp.width), BLUE)
    draw = ImageDraw.Draw(img)

    # Try a bigger font, fallback to default if missing
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Lines of text
    line1 = "Hello Otto!"
    line2 = "GO BLUE!"
    line3 = time.strftime("%I:%M:%S %p")

    # Center text by measuring width
    for i, text in enumerate([line1, line2, line3]):
        fnt = font_large if i < 2 else font_small
        bbox = draw.textbbox((0,0), text, font=fnt)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (disp.height - w) // 2
        y = 20 + i * 40
        draw.text((x, y), text, font=fnt, fill=MAIZE)

    img = img.rotate(180)
    disp.ShowImage(img)

def main():
    disp = LCD_2inch.LCD_2inch()
    disp.Init()
    disp.clear()

    try:
        disp.bl_DutyCycle(80)  # brighter
    except:
        pass

    try:
        while True:
            draw_once(disp)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        disp.module_exit()

if __name__ == "__main__":
    main()
