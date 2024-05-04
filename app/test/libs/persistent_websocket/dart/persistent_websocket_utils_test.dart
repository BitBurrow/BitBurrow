import 'package:test/test.dart';
import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:bitburrow/persistent_websocket.dart';

String unmodTest() {
  final random = math.Random();
  final windowSizes = [10, 100, 1000, 10000, 16384, 32768, 8322];
  for (final win in windowSizes) {
    for (var i = 0; i < 1000000; i++) {
      final short = random.nextInt(win);
      final long = random.nextInt(0xFFFFFF);
      final n = unmod(short, long, w: win);
      if (n % win == short) {
        if ((long - n).abs() <= win ~/ 2) continue;
      }
      return ("failed: unmod($short, $long, $win) == $n");
    }
  }
  return ('success');
}

Future<bool> timekeeperTest() async {
  var start = DateTime.now();
  var stringBuffer = StringBuffer();

  void log(String s) {
    var elapsed = DateTime.now().difference(start).inSeconds;
    stringBuffer.write('${elapsed.toString().padLeft(3, ' ')}s: $s\n');
  }

  void fourSeconds() => log("            four seconds");
  void fiveSeconds() => log("                         five seconds");
  void twoSeconds() => log("two seconds");

  Future<void> demoTimekeeper() async {
    var a = Timekeeper.periodic(4, fourSeconds);
    var b = Timekeeper(5, fiveSeconds);
    var c = Timekeeper.exponential(2, twoSeconds, 2, 45);
    log("zero seconds");
    await Future.delayed(const Duration(seconds: 30));
    log("            canceling four");
    a.cancel();
    await Future.delayed(const Duration(seconds: 7));
    log("done");
  }

  await demoTimekeeper();
  String expected = """
      0s: zero seconds
      2s: two seconds
      4s:             four seconds
      5s:                          five seconds
      6s: two seconds
      8s:             four seconds
     12s:             four seconds
     14s: two seconds
     16s:             four seconds
     20s:             four seconds
     24s:             four seconds
     28s:             four seconds
     30s:             canceling four
     30s: two seconds
     37s: done
    """;
  var indent6 = RegExp(r'^ {4}', multiLine: true);
  var unindented = expected.replaceAllMapped(indent6, (match) => '');
  return stringBuffer.toString().trim() == unindented.trim();
}

bool printableHexTest() {
  var chunkTest = "1234\x0056789\x01\x02abcd\nefg\nhi\nhello\n\n"
      "hello\n\n\nshouldn't \\ backslash\xe2\x9c\x94 done\n";
  var chunkTestOut =
      "'1234' 00 '56789' 01 02 'abcd' 0A 65 66 67 0A 68 69 0A 'hello' 0A 0A "
      "'hello' 0A 0A 0A 'shouldn' 27 't \\ backslash' E2 9C 94 ' done' 0A";
  return printableHex(Uint8List.fromList(chunkTest.codeUnits)) == chunkTestOut;
}

void main() {
  group('PersistentWebSocket tests', () {
    test('unmod', () {
      expect(unmodTest(), 'success');
    });
    test('timekeeper', () async {
      expect(await timekeeperTest(), true);
    }, timeout: const Timeout(Duration(minutes: 2)));
    test('printableHex', () {
      expect(printableHexTest(), true);
    });
    test('parseIpPort', () {
      expect(
          parseIpPort('example.org'),
          equals({
            'host': 'example.org',
            'port': 0,
          }));
      expect(
          parseIpPort('example.org:80'),
          equals({
            'host': 'example.org',
            'port': 80,
          }));
      expect(
          parseIpPort('192.168.100.99'),
          equals({
            'host': '192.168.100.99',
            'port': 0,
          }));
      expect(
          parseIpPort('192.168.100.99:8888'),
          equals({
            'host': '192.168.100.99',
            'port': 8888,
          }));
      expect(
          parseIpPort('[fe80::d4a8:6435:f54c:1f4e]'),
          equals({
            'host': 'fe80::d4a8:6435:f54c:1f4e',
            'port': 0,
          }));
      expect(
          parseIpPort('[fe80::d4a8:6435:f54c:1f4e]:995'),
          equals({
            'host': 'fe80::d4a8:6435:f54c:1f4e',
            'port': 995,
          }));
      expect(
          parseIpPort('[::1]'),
          equals({
            'host': '::1',
            'port': 0,
          }));
      expect(
          parseIpPort('[::1]:22'),
          equals({
            'host': '::1',
            'port': 22,
          }));
      expect(
          parseIpPort('example.org', 443),
          equals({
            'host': 'example.org',
            'port': 443,
          }));
      expect(
          parseIpPort('[::1]', 443),
          equals({
            'host': '::1',
            'port': 443,
          }));
      expect(
          parseIpPort('[::1]:8443', 443),
          equals({
            'host': '::1',
            'port': 8443,
          }));
    });
    test('formatIpPort', () {
      expect(formatIpPort('example.org', 80), equals('example.org:80'));
      expect(formatIpPort('10.80.80.205', 1234), equals('10.80.80.205:1234'));
      expect(formatIpPort('fe80::d4a8:6435:f54c:1f4e', 22),
          equals('[fe80::d4a8:6435:f54c:1f4e]:22'));
    });
  });
}
