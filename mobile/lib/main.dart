// TestWay — Android ilova. Render'dagi Mini App'ni WebView ichida ochadi.
//
// Telegram ichida bo'lmagani uchun kirish bot orqali tasdiqlanadi (sayt o'zi
// "Telegram orqali kirish" sahifasini ko'rsatadi). Ilova faqat:
//  - user-agent'ga "TestWayApp" qo'shadi (sayt shu bo'yicha ilova ekanini biladi);
//  - t.me va boshqa tashqi havolalarni Telegram/brauzerda ochadi;
//  - Android "orqaga" tugmasini saytdagi "←" ga ulaydi;
//  - internet yo'q bo'lsa, qayta urinish oynasini ko'rsatadi;
//  - jonli skaner uchun kameraga ruxsat beradi (sayt so'raganda).

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:url_launcher/url_launcher.dart';

const appUrl = 'https://bo-tlarim.onrender.com/';
final appHost = Uri.parse(appUrl).host;

const _navy = Color(0xFF1B2452);
const _background = Color(0xFFF6F7FB);

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const TestWayApp());
}

class TestWayApp extends StatelessWidget {
  const TestWayApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TestWay',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: _navy, scaffoldBackgroundColor: _background),
      home: const WebShell(),
    );
  }
}

class WebShell extends StatefulWidget {
  const WebShell({super.key});

  @override
  State<WebShell> createState() => _WebShellState();
}

class _WebShellState extends State<WebShell> {
  InAppWebViewController? _controller;
  double _progress = 0;
  bool _offline = false;

  Future<void> _openExternal(WebUri uri) async {
    try {
      await launchUrl(Uri.parse(uri.toString()), mode: LaunchMode.externalApplication);
    } catch (_) {
      // Telegram o'rnatilmagan bo'lsa — havola brauzerda ochiladi
      if (uri.scheme == 'tg') return;
      await launchUrl(Uri.parse(uri.toString()), mode: LaunchMode.platformDefault);
    }
  }

  Future<void> _handleBack() async {
    final handled = await _controller?.evaluateJavascript(
      source: 'window.appBack ? window.appBack() : false',
    );
    if (handled == true) return;
    await SystemNavigator.pop();
  }

  void _retry() {
    setState(() => _offline = false);
    _controller?.reload();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _handleBack();
      },
      child: Scaffold(
        body: SafeArea(
          child: Stack(
            children: [
              InAppWebView(
                initialUrlRequest: URLRequest(url: WebUri(appUrl)),
                initialSettings: InAppWebViewSettings(
                  applicationNameForUserAgent: 'TestWayApp/1.0',
                  javaScriptEnabled: true,
                  domStorageEnabled: true,
                  useShouldOverrideUrlLoading: true,
                  supportMultipleWindows: true,
                  javaScriptCanOpenWindowsAutomatically: true,
                  transparentBackground: true,
                  mediaPlaybackRequiresUserGesture: false,
                  allowsInlineMediaPlayback: true,
                  supportZoom: false,
                ),
                onWebViewCreated: (controller) => _controller = controller,
                // Faqat o'z saytimiz ilova ichida; qolgani (t.me, Payme, Click) — tashqarida
                shouldOverrideUrlLoading: (controller, action) async {
                  final uri = action.request.url;
                  if (uri == null) return NavigationActionPolicy.ALLOW;
                  final isOwnSite = uri.host == appHost && (uri.scheme == 'https' || uri.scheme == 'http');
                  final isInternal = uri.scheme == 'about' || uri.scheme == 'blob' || uri.scheme == 'data';
                  if (isOwnSite || isInternal) return NavigationActionPolicy.ALLOW;
                  await _openExternal(uri);
                  return NavigationActionPolicy.CANCEL;
                },
                // window.open(...) — yangi oyna o'rniga tashqi ilova
                onCreateWindow: (controller, action) async {
                  final uri = action.request.url;
                  if (uri != null) await _openExternal(uri);
                  return false;
                },
                // Jonli skaner kamerani so'raydi — Android ruxsati olinib, faqat o'z saytimizga beriladi
                onPermissionRequest: (controller, request) async {
                  final wantsCamera = request.resources.contains(PermissionResourceType.CAMERA);
                  final ownSite = request.origin.host == appHost;
                  final granted = wantsCamera && ownSite && (await Permission.camera.request()).isGranted;
                  return PermissionResponse(
                    resources: request.resources,
                    action: granted ? PermissionResponseAction.GRANT : PermissionResponseAction.DENY,
                  );
                },
                onProgressChanged: (controller, progress) => setState(() => _progress = progress / 100),
                // Sahifa ko'rinishi bilan chiziq yo'qoladi — tashqi skript/shrift sekin yuklansa ham qolib ketmaydi
                onPageCommitVisible: (controller, url) => setState(() => _progress = 1),
                onLoadStop: (controller, url) => setState(() => _progress = 1),
                onReceivedError: (controller, request, error) {
                  if (request.isForMainFrame ?? true) setState(() => _offline = true);
                },
              ),
              if (_progress < 1) LinearProgressIndicator(value: _progress, minHeight: 3, color: _navy),
              if (_offline) _OfflineView(onRetry: _retry),
            ],
          ),
        ),
      ),
    );
  }
}

class _OfflineView extends StatelessWidget {
  const _OfflineView({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: _background,
      padding: const EdgeInsets.all(32),
      alignment: Alignment.center,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('📡', style: TextStyle(fontSize: 56)),
          const SizedBox(height: 16),
          const Text(
            'Internet bilan aloqa yo\'q',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: _navy),
          ),
          const SizedBox(height: 8),
          const Text(
            'Internetni tekshirib, qayta urinib ko\'ring.',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.black54),
          ),
          const SizedBox(height: 24),
          FilledButton(
            onPressed: onRetry,
            style: FilledButton.styleFrom(backgroundColor: _navy, minimumSize: const Size(200, 48)),
            child: const Text('Qayta urinish'),
          ),
        ],
      ),
    );
  }
}
