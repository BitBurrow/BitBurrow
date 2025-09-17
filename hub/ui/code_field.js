const maxLength = 21;
const maxLengthPure = 18;
const allowedChars = '23456789BCDFGHJKLMNPQRSTVWXZ';
const dashesAt = [4, 10, 15];
const dashesAfter = [4 - 1, 10 - 2, 15 - 3];

var varifyCode = (str) => {
    for (var i = 0; i < str.length; i++) {
        if (dashesAt.includes(i)) {
            if (str[i] != '-') {
                return false;

            }
        } else {
            if (!allowedChars.includes(str[i])) {
                return false;
            }
        }
    }
    if (str.length != maxLength) {
        return false;
    }
    return true;
};

var filterChars = (chars) => {
    chars = chars.toUpperCase();
    var out = '';
    for (var i = 0; i < chars.length; i++) {
        if (allowedChars.includes(chars[i])) {
            out += chars[i];
        }
    }
    return out;
};

var setInputField = (field) => {
    const input = field;

    //These variables are to fix a bug where a paste will leave the curser too far left due to the newly incerted dashes
    var pasteMoveRight = false;
    var dashesNum;

    var lastInputState = input.value;
    input.addEventListener('input', (e) => {
        var typingPlace = input.selectionStart;
        if (input.value[typingPlace] != '-' && lastInputState[typingPlace] == '-' && input.value.length < lastInputState.length) {
            input.value = input.value.slice(0, typingPlace - 1) + input.value.slice(typingPlace);
            typingPlace--;
        }
        var rawIn = filterChars(input.value);
        var out = '';
        for (var i = 0; i < rawIn.length; i++) {
            out += rawIn[i];
            if (dashesAfter.includes(i)) {
                out += '-';
            }
        }
        input.value = out;
        if (out.length > maxLength) {
            input.value = lastInputState;
            input.value = input.value.substring(0, Math.min(input.value.length, maxLength));
        }
        if (lastInputState == input.value) {
            typingPlace = lastInputState.length;
        }
        else {
            lastInputState = input.value;
            if (out[typingPlace] == '-') { typingPlace++; }
            if (pasteMoveRight) {
                pasteMoveRight = false;
                var amount = input.value.split("-").length - 1 - dashesNum;
                typingPlace += amount;
            }
        }
        input.selectionStart = typingPlace;
        input.selectionEnd = typingPlace;
    });
    input.addEventListener('paste', (e) => {
        pasteMoveRight = true;
        dashesNum = input.value.split('-').length - 1;
        dashesNum -= input.value.substring(input.selectionStart, input.selectionEnd).split('-').length - 1;
    });
};