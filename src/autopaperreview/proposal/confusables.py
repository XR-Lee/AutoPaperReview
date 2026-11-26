"""ASCII lookalikes derived from ICU 76.1's Unicode 16.0 confusable skeletons.

Group non-ASCII characters by the ASCII character with the same skeleton.
ASCII text is left intact. Where ASCII targets share a skeleton, prefer
lowercase letters, then uppercase letters. Keep the mapping versioned so
text-cleaning receipts can distinguish changes to the data.

Source: https://www.unicode.org/Public/security/16.0.0/confusables.txt
Copyright © 1991-2024 Unicode, Inc.
Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the
Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall
be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS
OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

ASCII_LOOKALIKES = {
    '-': '˗۔‐‑‒–⁃−➖Ⲻ﹘',
    '/': '᜵⁁⁄∕╱⟋⧸Ⳇ⼃〳ノ㇓丿𝈺',
    '2': 'ƧϨᒿꙄꛯꝚ',
    '3': 'ƷȜЗӠⳌꝪꞫ𑣊𖼻𝈆',
    '4': 'Ꮞ𑢯',
    '5': 'Ƽ𑢻',
    '6': 'бᏮⳒ𑣕',
    '7': '𐓒𑣆𝈒',
    '8': 'Ȣȣ৪੪𐌚𞣋',
    '9': '৭੧୨൭ⳊꝮ𑢬𑣌𑣖',
    ':': 'ː˸։׃܃܄᛬᠃᠉⁚∶ꓽ꞉',
    'A': 'ΑАᎪᗅꓮ𐊠𖽀𝚨𝛢𝜜𝝖𝞐',
    'B': 'ΒВᏴᗷꓐꞴ𐊂𐊡𐌁𝚩𝛣𝜝𝝗𝞑',
    'C': 'ϹСᏟⲤꓚ𐊢𐌂𐐕𐔜𑣩𑣲🝌',
    'D': 'Ꭰᗞᗪꓓ',
    'E': 'ΕЕᎬ⋿ⴹꓰ𐊆𑢦𑢮𝚬𝛦𝜠𝝚𝞔',
    'F': 'ϜᖴꓝꞘ𐊇𐊥𐔥𑢢𑣂𝈓𝟊',
    'G': 'ԌᏀᏳꓖ',
    'H': 'ΗНᎻᕼⲎꓧ𐋏𝚮𝛨𝜢𝝜𝞖',
    'J': 'ͿЈᎫᒍꓙꞲ',
    'K': 'ΚКᏦᛕⲔꓗ𐔘𝚱𝛫𝜥𝝟𝞙',
    'L': 'ᏞᒪⳐꓡ𐐛𐔦𑢣𑢲𖼖𝈪',
    'M': 'ΜϺМᎷᗰᛖⲘꓟ𐊰𐌑𝚳𝛭𝜧𝝡𝞛',
    'N': 'ΝⲚꓠ𐔓𝚴𝛮𝜨𝝢𝞜',
    'O': 'ΟОՕ߀০ଠ୦ዐⲞⵔ〇ꓳ𐊒𐊫𐐄𐓂𐔖𑓐𑢵𑣠𝚶𝛰𝜪𝝤𝞞',
    'P': 'ΡРᏢᑭⲢꓑ𐊕𝚸𝛲𝜬𝝦𝞠',
    'Q': 'ⵕ',
    'R': 'ƦᎡᏒᖇꓣ𐒴𖼵𝈖',
    'S': 'ЅՏᏕᏚꓢ𐊖𐐠𖼺',
    'T': 'ΤТᎢ⊤⟙Ⲧꓔ𐊗𐊱𐌕𑢼𖼊𝚻𝛵𝜯𝝩𝞣🝨',
    'U': 'Սሀᑌ∪⋃ꓴ𐓎𑢸𖽂',
    'V': 'Ѵ٧۷Ꮩᐯⴸꓦꛟ𐔝𑢠𖼈𝈍',
    'W': 'ԜᎳᏔꓪ𑣦𑣯',
    'X': 'ΧХ᙭ᚷ╳ⲬⵝꓫꞳ𐊐𐊴𐌗𐌢𐔧𑣬𝚾𝛸𝜲𝝬𝞦',
    'Y': 'ΥϒУҮᎩᎽⲨꓬ𐊲𑢤𖽃𝚼𝛶𝜰𝝪𝞤',
    'Z': 'ΖᏃꓜ𐋵𑢩𑣥𝚭𝛧𝜡𝝛𝞕',
    'a': 'ɑαа⍺𝛂𝛼𝜶𝝰𝞪',
    'b': 'ƄЬᏏᑲᖯ',
    'c': 'ϲсᴄⲥꮯ𐐽',
    'd': 'ԁᏧᑯꓒ',
    'e': 'еҽ℮ꬲ',
    'f': 'քẝꞙꬵ',
    'g': 'ƍɡցᶃ',
    'h': 'һհᏂ',
    'i': 'ıɩɪ˛ͺιіӏᎥι⍳ꙇꭵ𑣃𝚤𝛊𝜄𝜾𝝸𝞲',
    'j': 'ϳј',
    'l': 'ƖǀΙІӀ׀וןا١۱ߊᛁ∣⏽Ⲓⵏꓲﺍﺎ￨𐊊𐌉𐌠𖼨𝚰𝛪𝜤𝝞𝞘𞣇𞸀𞺀',
    'm': '𑜀𑣣',
    'n': 'ոռ',
    'o': 'οσоօסه٥ھہە۵०੦૦௦౦೦ഠ൦๐໐ဝ၀ჿᴏᴑⲟꬽﮦﮧﮨﮩﮪﮫﮬﮭﻩﻪﻫﻬ𐐬𐓪𑣈𑣗𝛐𝛔𝜊𝜎𝝄𝝈𝝾𝞂𝞸𝞼𞸤𞹤𞺄',
    'p': 'ρϱр⍴ⲣ𝛒𝛠𝜌𝜚𝝆𝝔𝞀𝞎𝞺𝟈',
    'q': 'ԛգզ',
    'r': 'гᴦⲅꭇꭈꮁ',
    's': 'ƽѕꜱꮪ𐑈𑣁',
    'u': 'ʋυսᴜꞟꭎꭒ𐓶𑣘𝛖𝜐𝝊𝞄𝞾',
    'v': 'νѵטᴠ∨⋁ꮩ𑜆𑣀𝛎𝜈𝝂𝝼𝞶',
    'w': 'ɯѡԝաᴡꮃ𑜊𑜎𑜏',
    'x': '×хᕁᕽ᙮⤫⤬⨯',
    'y': 'ɣʏγуүყᶌỿℽꭚ𑣜𝛄𝛾𝜸𝝲𝞬',
    'z': 'ᴢꮓ𑣄',
}
