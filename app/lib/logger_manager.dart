import 'package:logging/logging.dart';
import 'parent_form_state.dart';

const gorouterLogMessage = "𝌤𝌤 new page:";

class LoggerManager {
  LogRecord? _lastLog;
  Future? _lastLogTimeout;

  LoggerManager() {
    Logger.root.level = Level.FINE;
    Logger.root.onRecord.listen(filterAndLogOutput);
  }

  filterAndLogOutput(LogRecord r) {
    // for now, hide GoRouter logging
    if (r.loggerName == 'GoRouter' /*&& !r.message.startsWith("pushing ")*/) {
      return;
    }
    // skip near-duplicate but confusing GoRouter page change messages
    if (r.loggerName == 'main' && r.message.startsWith(gorouterLogMessage)) {
      if (_lastLog == null) {
        _lastLog = r; // store for next time
        _lastLogTimeout = Future.delayed(const Duration(milliseconds: 100), () {
          if (_lastLog != null) {
            _logOutput(_lastLog!);
            _lastLog = null;
          }
        });
        return;
      } else {
        var diff = r.time.difference(_lastLog!.time).inMilliseconds;
        if (diff < 100) {
          _lastLog = r; // overwrite older entry
          _lastLogTimeout =
              Future.delayed(const Duration(milliseconds: 100), () {
            if (_lastLog != null) {
              _logOutput(_lastLog!);
              _lastLog = null;
            }
          });
          return;
        }
      }
    }
    if (_lastLog != null) {
      _logOutput(_lastLog!);
      _lastLog = null;
    }
    _logOutput(r);
  }

  _logOutput(LogRecord r) {
    // for security, partially redact anything that looks like a login key
    final String m1 = r.message.replaceAllMapped(accountRE, accountREReplace);
    final String m2 = m1.replaceAllMapped(pureAccountRE, pureAccountREReplace);
    // ignore: avoid_print
    print("${r.time.toString().substring(0, 22)} "
        "[${r.loggerName}] ${r.level.name}: $m2");
  }
}
