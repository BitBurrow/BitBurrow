import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';

void main() {
  runApp(App());
}

Page<void> ourPageBuilder(
        BuildContext context, GoRouterState state, Widget child) =>
    CustomTransitionPage<void>(
      key: state.pageKey,
      child: child,
      transitionsBuilder: // disable the default MaterialApp transition
          (context, animation, secondaryAnimation, child) =>
              FadeTransition(opacity: animation, child: child),
    );

class App extends StatelessWidget {
  App({Key? key}) : super(key: key);

  static const String title = 'BitBurrow';

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(0xFF343A40),
          secondary: const Color(0xFFFFC107),
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
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, RootScreen()),
        routes: <GoRoute>[
          // has back arrow to root page
          GoRoute(
            path: 'page2',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, Page2Screen()),
          ),
        ],
      ),
      GoRoute(
        path: '/page3',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, Page3Screen()),
      ),
    ],
    urlPathStrategy:
        UrlPathStrategy.path, // turn off the extra `#/` in the URLs
  );
}

var text1 =
    'Ut **bold** and *ital* and [pub.dev](https://pub.dev) necessitatibus '
    '[page 2 with back arrow](/page2) and [page 3](/page3) and '
    'dignissimos rerum et fuga sapiente et dicta internos non '
    'odio repudiandae? Ut repellat amet est ducimus doloremque est similique nobis '
    'qui explicabo molestiae. Qui sunt porro vel quas officia nam porro galisum! '
    '\n\n';
var text2 =
    'Lorem ipsum dolor sit amet. Non magni internos eum quis omnis et eveniet '
    'repellendus eos illo quas est voluptatibus minima ut explicabo enim. Ea nobis '
    'corporis sit voluptas nihil in labore minima quo similique velit ex distinctio '
    'laboriosam vel iste excepturi non sunt alias. Et eligendi odio est impedit '
    'voluptatem et animi necessitatibus ut quod dignissimos non aspernatur veniam '
    'aut illum minima. Qui iure facere id mollitia doloribus et eaque nemo vel enim '
    'molestiae et consequuntur quas et quae quia. '
    '\n\n'
    'Aut magnam incidunt et '
    'earum voluptate vel voluptates minus et illo et necessitatibus doloribus et '
    'temporibus accusamus. In vitae alias non corrupti ullam ad galisum velit. Ea '
    'laudantium minus id quam quae rem illum magnam id provident veniam id facilis '
    'sunt ad rerum officiis sed minima unde. '
    '\n\n';
var text3 =
    'Ut Quis sint ea sequi assumenda qui ullam fuga et debitis tenetur id dolores '
    'dolorum quo vitae dolores quo odit obcaecati! Id fugiat temporibus qui '
    'doloremque adipisci ut consectetur impedit hic possimus molestiae ea iste '
    'saepe. Ut eligendi error nam cumque magnam et dolorem omnis et enim ratione '
    'sed repellat fugiat eum perspiciatis facilis et voluptatem quod. Sed voluptas '
    'repudiandae et recusandae excepturi aut eligendi laborum et obcaecati dolorem '
    'et quidem nisi et voluptate quod vel numquam illo! '
    '\n\n';

void onMarkdownClick(BuildContext context, String url) {
  if (url[0] == '/') {
    // within our app
    context.go(url);
  } else {
    launchUrl(Uri.parse(url));
  }
}

Widget ourScreenLayout(BuildContext context, String text) => Scaffold(
      appBar: AppBar(
        // title: const Text(App.title),
        toolbarHeight: 40.0,
      ),
      body: SingleChildScrollView(
          child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(18.0),
            child: MarkdownBody(
                selectable:
                    false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
                //FIXME: read from a file: https://developer.school/tutorials/how-to-display-markdown-in-flutter
                styleSheet: MarkdownStyleSheet.fromTheme(ThemeData(
                    textTheme: const TextTheme(
                        bodyText2: TextStyle(
                  fontSize: 16.0,
                  color: Colors.black,
                )))),
                onTapLink: (text, url, title) {
                  onMarkdownClick(context, url!);
                },
                data: text),
          ),
        ],
      )),
    );

class RootScreen extends StatelessWidget {
  const RootScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        text1 + text2 * 15 + text3,
      );
}

class Page2Screen extends StatelessWidget {
  const Page2Screen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text(App.title)),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              ElevatedButton(
                onPressed: () => context.go('/'),
                child: const Text('Go back to home page'),
              ),
            ],
          ),
        ),
      );
}

class Page3Screen extends StatelessWidget {
  const Page3Screen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text(App.title)),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              ElevatedButton(
                onPressed: () => context.go('/'),
                child: const Text('Go back to home page'),
              ),
            ],
          ),
        ),
      );
}
