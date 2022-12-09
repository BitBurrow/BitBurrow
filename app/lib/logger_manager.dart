import 'package:flutter/foundation.dart' as foundation;
import 'package:logging/logging.dart';
import 'parent_form_state.dart';

const gorouterLogMessage = "𝌤𝌤 new page:";

class LoggerManager {
  LogRecord? _lastLog;
  Future? _lastLogTimeout;
  var buffer = StringBuffer();

  LoggerManager() {
    Logger.root.level = Level.FINE;
    Logger.root.onRecord.listen(filterAndLogOutput);
  }

  _logOutput(LogRecord r) {
    // for security, partially redact anything that looks like a login key
    final String m1 = r.message.replaceAllMapped(accountRE, accountREReplace);
    final String m2 = m1.replaceAllMapped(pureAccountRE, pureAccountREReplace);
    final String m3 = "${r.time.toString().substring(0, 22)} "
        "[${r.loggerName}] ${r.level.name}: $m2";
    if (foundation.kDebugMode) {
      print(m3); // ignore: avoid_print
    }
    buffer.writeln(m3);
  }

  _lastLogOutput() {
    if (_lastLog != null) {
      _logOutput(_lastLog!);
      _lastLog = null;
    }
  }

  // store log in case it's not actually the correct entry
  void _stashLogBriefly(LogRecord r) {
    _lastLog = r; // store for next time
    _lastLogTimeout = Future.delayed(const Duration(milliseconds: 100), () {
      _lastLogOutput();
    });
  }

  filterAndLogOutput(LogRecord r) {
    // for now, hide GoRouter logging
    if (r.loggerName == 'GoRouter' /*&& !r.message.startsWith("pushing ")*/) {
      return;
    }
    // skip near-duplicate but confusing GoRouter page change messages
    if (r.loggerName == 'main' && r.message.startsWith(gorouterLogMessage)) {
      if (_lastLog == null) {
        return _stashLogBriefly(r);
      } else {
        var diff = r.time.difference(_lastLog!.time).inMilliseconds;
        if (diff < 100) {
          return _stashLogBriefly(r); // overwrite older, wrong log
        }
      }
    }
    _lastLogOutput();
    _logOutput(r);
  }
}
