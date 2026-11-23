"""Рисует assets/icon.ico (микрофон на красном круге). Запуск: python assets/make_icon.py"""
import pathlib

from PIL import Image, ImageDraw

img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse((12, 12, 244, 244), fill="#d62828")
d.rounded_rectangle((100, 60, 156, 150), 28, fill="white")
d.arc((78, 100, 178, 180), 0, 180, fill="white", width=10)
d.line((128, 180, 128, 208), fill="white", width=10)
d.line((100, 208, 156, 208), fill="white", width=10)
img.save(pathlib.Path(__file__).with_name("icon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
