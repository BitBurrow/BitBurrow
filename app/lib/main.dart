import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:logging/logging.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart' as storage;
import 'dart:io' as io;
import 'welcome_screen.dart';
import 'logger_manager.dart';
import 'new_login_key_screen.dart';
import 'sign_in_screen.dart';
import 'servers_screen.dart';
import 'new_server_screen.dart';
import 'package:flutter_web_plugins/url_strategy.dart';

final _log = Logger('main');
var loginState = LoginState.instance;

void main() async {
  usePathUrlStrategy(); // turn off the extra `#/` in the URLs
  LoggerManager();
  _log.info("Begin Bitburrow app");
  WidgetsFlutterBinding.ensureInitialized();
  await onAppStart();
  runApp(App());
}

class LoginState {
  String hub = ""; // saved in secure storage
  String coupon = "";
  String newLoginKey = "";
  String loginKey = ""; // saved in secure storage
  bool loginKeyVerified = false; // saved in secure storage
  bool saveLoginKey = false; // user choice to keep login key in secure storage
  bool skipWelcomeScreen = true; // forward past welcome screen if signed in
  List<dynamic> servers = [];

  bool isSignedIn() => loginKeyVerified;
  bool isNotSignedIn() => !loginKeyVerified;
  String get pureCoupon => coupon.replaceAll('-', ''); // for API calls
  String get pureLoginKey => loginKey.replaceAll('-', ''); // for API calls

  static LoginState? _instance; // singleton--one instance for the app
  LoginState._();

  static LoginState get instance {
    _instance ??= LoginState._();
    return _instance!;
  }

  // convert to display version used in this app, e.g. 'X88L-7V2BC-MM3P-RKVF2'
  String dressLoginKey(String pureLoginKey) {
    return '${pureLoginKey.substring(0, 4)}-${pureLoginKey.substring(4, 9)}-'
        '${pureLoginKey.substring(9, 13)}-${pureLoginKey.substring(13)}';
  }
}

Future<void> onAppStart() async {
  _log.fine("begin onAppStart()");
  // fixme: user sees blank screen until this completes; implement a loading
  // ... screen if this method takes too long
  try {
    var keyStore = const storage.FlutterSecureStorage();
    loginState.hub = (await keyStore.read(key: 'hub')) ?? "";
    _log.info("Loaded from secure storage: hub ${loginState.hub}");
    loginState.loginKey = (await keyStore.read(key: 'login_key')) ?? "";
    _log.info("Loaded from secure storage: "
        "login key ${loginState.loginKey}");
    loginState.loginKeyVerified =
        (await keyStore.read(key: 'login_key_verified') == 'true');
    _log.info("Loaded from secure storage: "
        "verified status '${loginState.loginKeyVerified}'");
    // login key is saved if and only if it's verified and user opts to store it
    loginState.saveLoginKey = loginState.loginKeyVerified;
  } catch (err) {
    if (io.Platform.environment['DBUS_SESSION_BUS_ADDRESS'] == 'disabled:') {
      _log.warning("B70101 D-Bus has been disabled; add this to .bashrc: "
          "`unset DBUS_SESSION_BUS_ADDRESS` (VSCode Linux issue - "
          "https://github.com/electron/electron/issues/31981)");
    } else {
      _log.warning("B52325 can't read from secure storage: $err");
    }
    return;
  }
  _log.fine("end onAppStart()");
}

Page<void> ourPageBuilder(
    BuildContext context, GoRouterState state, Widget child) {
  _log.info("$gorouterLogMessage $child");
  return CustomTransitionPage<void>(
    key: state.pageKey,
    child: child,
    transitionsBuilder: // change the default MaterialApp transition
        (context, animation, secondaryAnimation, child) =>
            FadeTransition(opacity: animation, child: child),
  );
}

class App extends StatelessWidget {
  App({Key? key}) : super(key: key);

  static const String title = 'BitBurrow';

  static const ourPrimaryColor = 0xff5b5b5b;
  final int r = (ourPrimaryColor & 0x00ff0000) ~/ 0x10000;
  final int g = (ourPrimaryColor & 0x0000ff00) ~/ 0x100;
  final int b = (ourPrimaryColor & 0x000000ff);
  late final Map<int, Color> ourColorCodes = {
    50: Color.fromRGBO(r, g, b, .1),
    100: Color.fromRGBO(r, g, b, .2),
    200: Color.fromRGBO(r, g, b, .3),
    300: Color.fromRGBO(r, g, b, .4),
    400: Color.fromRGBO(r, g, b, .5),
    500: Color.fromRGBO(r, g, b, .6),
    600: Color.fromRGBO(r, g, b, .7),
    700: Color.fromRGBO(r, g, b, .8),
    800: Color.fromRGBO(r, g, b, .9),
    900: Color.fromRGBO(r, g, b, 1),
  };

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        primarySwatch:
            MaterialColor(ourPrimaryColor, ourColorCodes), // checkboxes
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(ourPrimaryColor),
          secondary: const Color(0xffd3a492),
          background: const Color(0xffff0000), // seems unused
        ),
      ),
      routeInformationProvider: _router.routeInformationProvider,
      routeInformationParser: _router.routeInformationParser,
      routerDelegate: _router.routerDelegate,
    );
  }

  final GoRouter _router = GoRouter(
    routes: <GoRoute>[
      GoRoute(
        path: '/',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const WelcomeScreen()),
      ),
      GoRoute(
        path: '/new-login-key',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const NewLoginKeyScreen()),
      ),
      GoRoute(
        path: '/sign-in',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const SignInScreen()),
      ),
      GoRoute(
        path: '/forget-login-key',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const SignInScreen()),
      ),
      GoRoute(
        path: '/servers',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const ServersScreen()),
      ),
      GoRoute(
        path: '/new-server',
        pageBuilder: (c, s) => ourPageBuilder(c, s, const NewServerScreen()),
      ),
    ],
    redirect: (context, state) {
      if (state.uri.toString() != state.matchedLocation) {
        _log.finer("GoRouter() redirect; location==${state.uri.toString()}");
      }
      _log.finer("GoRouter() redirect; subloc==${state.matchedLocation}");
      if (state.matchedLocation == '/' &&
          loginState.loginKeyVerified &&
          loginState.skipWelcomeScreen) {
        // if we have valid login key, forward to automatic sign-in
        return '/sign-in';
      }
      return null;
    },
  );
}

MarkdownBody textMd(BuildContext context, md) {
  return MarkdownBody(
    selectable:
        false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
    styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
        textScaleFactor: 1.14, // match default Text() widget size
        a: const TextStyle(
          color: Color.fromARGB(255, 40, 128, 22),
          decoration: TextDecoration.underline,
        ),
        p: const TextStyle(height: 1.35)), // less cramped vertically
    onTapLink: (text, url, title) {
      if (url == null) return;
      if (url[0] == '/') {
        context.push(url); // within our app
      } else {
        launchUrl(Uri.parse(url));
      }
    },
    data: md,
  );
}

Widget ourScreenLayout(BuildContext context, Widget body,
        {Widget? floatingActionButton}) =>
    Scaffold(
      appBar: AppBar(
          // title: const Text(App.title),
          toolbarHeight: 40.0,
          actions: <Widget>[
            PopupMenuButton<String>(
              icon: const Icon(Icons.more_vert),
              tooltip: "Menu",
              onSelected: (String value) {
                _log.fine("IconButton 'Show menu' onSelected('$value')");
                switch (value) {
                  case "Enter a coupon code":
                    // disable 'skip' so WelcomeScreen doesn't redirect
                    loginState.skipWelcomeScreen = false;
                    context.go('/');
                    break;
                  case "Sign in":
                    // mark login key NOT verified to skip auto-sign-in
                    loginState.loginKeyVerified = false;
                    context.go('/sign-in');
                    break;
                  case "Servers":
                    // don't call clearNavigatorRoutes() to erase history
                    context.push('/sign-in'); // will auto-sign-in if possible
                    break;
                  case "Forget login key":
                    // do not use push(); go() clears go_router history
                    context.go('/forget-login-key');
                    break;
                }
              },
              itemBuilder: (BuildContext context) {
                return {
                  "Enter a coupon code": 'images/ticket.svg',
                  "Sign in": 'images/key.svg',
                  "Servers": 'images/server.svg',
                  "Forget login key": 'images/x.svg',
                }
                    .entries
                    .map((item) => PopupMenuItem(
                          value: item.key,
                          child: Row(
                            children: [
                              SvgPicture.asset(item.value,
                                  width: 20,
                                  height: 20,
                                  colorFilter: ColorFilter.mode(
                                      item.key == "Forget login key"
                                          ? Colors.red // make 'X' icon red
                                          : Theme.of(context)
                                              .colorScheme
                                              .primary,
                                      BlendMode.srcIn)),
                              const SizedBox(width: 7),
                              Text(item.key),
                            ],
                          ),
                        ))
                    .toList();
              },
            ),
          ]),
      body: body,
      floatingActionButton: floatingActionButton,
    );
