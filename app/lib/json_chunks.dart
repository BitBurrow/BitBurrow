// ignore_for_file: avoid_print

import 'dart:convert' as convert;
import 'dart:typed_data';
import 'dart:async' as async;

class JsonChunks {
  JsonChunks(Stream<Uint8List> byteStream) {
    jsonObjectLengthTests(); // ensure Dart doesn't change error string
    byteStream.listen(
      (Uint8List data) async {
        _buffer = _buffer + String.fromCharCodes(data);
        int len;
        while (true) {
          // sink JSON chunks until there are no more
          len = jsonObjectLength(_buffer);
          if (len <= 0) break;
          // var forDisplay = _buffer
          //     .substring(0, len)
          //     .replaceAll(r'\', r'\\')
          //     .replaceAll('\r', r'\r')
          //     .replaceAll('\n', r'\n');
          // print("JsonChunks: sinking JSON: $forDisplay");
          _controller.sink.add(_buffer.substring(0, len));
          _buffer = _buffer.substring(len);
        }
        var forDisplay = _buffer
            .replaceAll(r'\', r'\\')
            .replaceAll('\r', r'\r')
            .replaceAll('\n', r'\n');
        if (len < 0) {
          print("B89703 ignoring bad JSON: $forDisplay");
          _buffer = '';
        } else {
          assert(len == 0);
          if (_buffer.isEmpty) {
            // print("JsonChunks: buffer is now empty");
          } else {
            // print("JsonChunks: holding partial JSON: $forDisplay");
          }
        }
      },
      onDone: () {
        _controller.close();
      },
      onError: (err) {
        print("B55049: $err");
        _controller.close();
      },
    );
  }

  String _buffer = '';

  final _controller = async.StreamController<String>();

  Stream<String> get stream => _controller.stream;

  void jsonObjectLengthTests() {
    // valid JSON object
    assert(jsonObjectLength('{"nums": [3]}') == 13);
    assert(jsonObjectLength('3') == 1);
    assert(jsonObjectLength('     [4]    ') == 12);
    assert(jsonObjectLength('{"three": 3}') == 12);
    // valid JSON object plus trailing data
    assert(jsonObjectLength('[4][6]') == 3);
    assert(jsonObjectLength('{\n"k":\n[21],\n"f": "g"}abc') == 22);
    assert(jsonObjectLength('\n{"3":4,"5":6}[3]') == 14);
    assert(jsonObjectLength('[7,\n6,\n5,4,\n\n3][18') == 15);
    // invalid JSON
    assert(jsonObjectLength('[1,,2]') == -1);
    assert(jsonObjectLength('{"one":1, "two", 2, "three":') == -1);
    assert(jsonObjectLength('{1,}') == -1);
    assert(jsonObjectLength('a') == -1);
    assert(jsonObjectLength('{1}') == -1);
    assert(jsonObjectLength("{'a':1}") == -1);
    // valid JSON so far, but incomplete
    assert(jsonObjectLength('[') == 0);
    assert(jsonObjectLength('[3') == 0);
    assert(jsonObjectLength('[3,') == 0);
    assert(jsonObjectLength('{') == 0);
    assert(jsonObjectLength('{"') == 0);
    assert(jsonObjectLength('{"t') == 0);
    assert(jsonObjectLength('{"three') == 0);
    assert(jsonObjectLength('{"three"') == 0);
    assert(jsonObjectLength('{"three":') == 0);
    assert(jsonObjectLength('{"three": ') == 0);
    assert(jsonObjectLength('{"three": 3') == 0);
    assert(jsonObjectLength('') == 0);
    assert(jsonObjectLength('\n') == 0);
    assert(jsonObjectLength('    ') == 0);
  }

  int jsonObjectLength(String buffer) {
    try {
      /*var json =*/ convert.jsonDecode(buffer);
      return buffer.length; // valid JSON, no trailing data
    } on FormatException catch (err) {
      Map<String, List<String>> regexList = {
        'okay': [
          // valid JSON object plus trailing data
          r'JSON.parse: unexpected non-whitespace character after JSON '
              r'data at line ([0-9]+) column ([0-9]+) of the JSON data',
        ],
        'bad': [
          // invalid JSON
          r'JSON.parse: expected ',
          r'JSON.parse: unexpected character at line [0-9]+ column [0-9]+ '
              r'of the JSON data',
        ],
        'notyet': [
          // valid JSON so far, but incomplete
          r'JSON.parse: end of data while reading object contents at '
              r'line [0-9]+ column [0-9]+ of the JSON data',
          r'JSON.parse: end of data when ',
          r'JSON.parse: unexpected end of data at line [0-9]+ column '
              r'[0-9]+ of the JSON data',
          r'JSON.parse: unterminated string literal at line [0-9]+ column '
              r'[0-9]+ of the JSON data',
          r'JSON.parse: unterminated string at line [0-9]+ column [0-9]+ '
              r'of the JSON data',
          r'JSON.parse: end of data after property',
          r'Unexpected end of input ',
          r'Unterminated string ',
        ],
        'okayorbad': [
          // may be 'okay' or 'bad'--need to test substring
          r'Unexpected character \(at character ([0-9]+)\)',
          r'Unexpected character \(at line ([0-9]+), character ([0-9]+)\)',
        ],
      };
      RegExpMatch? match;
      String? matchClass; // 'okay', 'bad', etc.
      outerLoop:
      for (matchClass in regexList.keys) {
        for (var regex in regexList[matchClass]!) {
          RegExp exp = RegExp(regex);
          match = exp.firstMatch(err.toString());
          if (match != null) break outerLoop;
        }
      }
      if (match == null) {
        print("B44125 unrecognized jsonDecode error: $err");
        return 0; // incomplete JSON object
      }
      if (matchClass == 'notyet') return 0;
      if (matchClass == 'bad') return -1;
      // matchClass is 'okay' or 'okayorbad'
      var line = 0;
      var col = 0;
      if (match.groupCount == 2) {
        // JSON object plus trailing data
        line = int.parse(match[1] ?? '1') - 1;
        col = int.parse(match[2] ?? '1') - 1;
      } else if (match.groupCount == 1) {
        // JSON object plus trailing data
        col = int.parse(match[1] ?? '1') - 1;
      }
      var offset = 0;
      for (var c in buffer.runes) {
        if (line == 0) {
          offset += col;
          break;
        }
        if (c == '\n'.codeUnitAt(0)) {
          line -= 1;
        }
        offset += 1;
      }
      if (matchClass == 'okay') {
        return offset;
      } else {
        try {
          convert.jsonDecode(buffer.substring(0, offset));
          return offset; // substring is valid
        } on FormatException {
          return -1; // invalid JSON
        }
      }
    }
  }
}
