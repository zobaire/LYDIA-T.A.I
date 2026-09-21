from PIL import Image
img = Image.open(r"C:\Users\book\Desktop\lydia_step2_2.png")
w, h = img.size
print("size", w, h)
# Dock is on the right ~560px wide (scaled). Crop right 45% of the screen, full height.
right = img.crop((int(w*0.55), 0, w, h))
right.save(r"C:\Users\book\Desktop\lydia_step2_2_right.png")
# Also crop the header strip of the dock (top 120px, right 45%)
head = img.crop((int(w*0.55), 0, w, 140))
head = head.resize((head.width*2, head.height*2), Image.LANCZOS)
head.save(r"C:\Users\book\Desktop\lydia_step2_2_head.png")
print("saved")
