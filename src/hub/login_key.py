###
### login key details (used for coupon codes too)
###

import re
import secrets
from typing import Final


# login key, e.g. 'X88L7V2BCMM3PRKVF2'
#     → log(28^18)÷log(2) ≈ 87 bits of entropy
# 6 words from 4000-word dictionary, e.g. 'OstrichPrecipiceWeldLinkRoastedLeopard'
#     → log(4000^6)÷log(2) ≈ 72 bits of entropy
# Mullvad 16-digit account number
#     → log(10^16)÷log(2) ≈ 53 bits of entropy
# Plus Codes use base 20 ('23456789CFGHJMPQRVWX'): https://en.wikipedia.org/wiki/Open_Location_Code
base28_digits: Final[str] = '23456789BCDFGHJKLMNPQRSTVWXZ'  # avoid bad words, 1/i, 0/O
login_key_len: Final[int] = 18
login_len: Final[int] = 4  # digits from beginning of login_key, used like a username
key_len: Final[int] = 14  # remaining digits of login_key, used like a password
base28_digit: Final[str] = f'[{base28_digits}]'
x4: Final[str] = '{4}'
x5: Final[str] = '{5}'
login_key_re: Final[re.Pattern] = re.compile(  # capture first 4 digits ('login' portion)
    f'({base28_digit}{x4}){base28_digit}{x5}{base28_digit}{x4}{base28_digit}{x5}'
)


def generate_login_key(n):  # create n digits of a new login_key
    return ''.join(secrets.choice(base28_digits) for i in range(n))


# def dress_login_key(k):  # display version, e.g. 'X88L-7V2BC-MM3P-RKVF2'
#    assert len(k) == login_key_len
#    return f'{k[0:4]}-{k[4:9]}-{k[9:13]}-{k[13:login_key_len]}'
