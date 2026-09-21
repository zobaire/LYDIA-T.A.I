import re
html = open('indexV2.html', encoding='utf-8').read()
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
print('num inline scripts:', len(scripts))
out = open('_extracted.js', 'w', encoding='utf-8')
for i, s in enumerate(scripts):
    out.write('// === script block ' + str(i) + ' ===\n' + s + '\n')
out.close()
print('wrote chars:', sum(len(s) for s in scripts))
