// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#include "web_player_tabs.hpp"

#include <QCoreApplication>
#include <QDesktopServices>
#include <QDir>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QResizeEvent>
#include <QStandardPaths>
#include <QTabWidget>
#include <QUrl>
#include <QVBoxLayout>

#if defined(CASU_HAVE_WEBVIEW2)
#include <windows.h>
#include <unknwn.h>
#include <atomic>
#include <functional>
#include "WebView2.h"
#endif

#if defined(CASU_HAVE_WEBENGINE)
#include <QtWebEngineWidgets/QWebEnginePage>
#include <QtWebEngineWidgets/QWebEngineProfile>
#include <QtWebEngineWidgets/QWebEngineView>
#endif

namespace mpcasu {

#if defined(CASU_HAVE_WEBVIEW2)

typedef HRESULT(STDAPICALLTYPE* CreateCoreWebView2EnvironmentWithOptionsFn)(
    PCWSTR browserExecutableFolder, PCWSTR userDataFolder,
    ICoreWebView2EnvironmentOptions* environmentOptions,
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler* environmentCreatedHandler);

namespace {

HMODULE get_webview2_loader() {
    static HMODULE hLoader = []() -> HMODULE {
        const QString exeDir = QCoreApplication::applicationDirPath();
        const QString dllPath = exeDir + QStringLiteral("/WebView2Loader.dll");
        HMODULE h = LoadLibraryW(reinterpret_cast<LPCWSTR>(dllPath.utf16()));
        if (!h) {
            h = LoadLibraryW(L"WebView2Loader.dll");
        }
        return h;
    }();
    return hLoader;
}

CreateCoreWebView2EnvironmentWithOptionsFn get_create_env_fn() {
    HMODULE h = get_webview2_loader();
    if (!h) return nullptr;
    return reinterpret_cast<CreateCoreWebView2EnvironmentWithOptionsFn>(
        GetProcAddress(h, "CreateCoreWebView2EnvironmentWithOptions"));
}

class EnvironmentCreatedHandler
    : public ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler {
public:
    using Func = std::function<HRESULT(HRESULT, ICoreWebView2Environment*)>;
    explicit EnvironmentCreatedHandler(Func fn) : fn_(std::move(fn)), ref_(1) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown ||
            riid == IID_ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler) {
            *ppv = static_cast<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++ref_; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r = --ref_;
        if (r == 0) delete this;
        return r;
    }
    HRESULT STDMETHODCALLTYPE Invoke(HRESULT res, ICoreWebView2Environment* env) override {
        return fn_ ? fn_(res, env) : S_OK;
    }

private:
    Func fn_;
    std::atomic<ULONG> ref_;
};

class NewWindowHandler : public ICoreWebView2NewWindowRequestedEventHandler {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** out) override {
        if (!out) return E_POINTER;
        if (id == IID_IUnknown || id == IID_ICoreWebView2NewWindowRequestedEventHandler) {
            *out = static_cast<ICoreWebView2NewWindowRequestedEventHandler*>(this);
            AddRef(); return S_OK;
        }
        *out = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG r = --refs_; if (!r) delete this; return r; }
    HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2* sender, ICoreWebView2NewWindowRequestedEventArgs* args) override {
        LPWSTR uri = nullptr;
        args->put_Handled(TRUE);
        if (SUCCEEDED(args->get_Uri(&uri)) && uri) {
            sender->Navigate(uri);
            CoTaskMemFree(uri);
        }
        return S_OK;
    }
private:
    std::atomic<ULONG> refs_{1};
};

class ControllerCreatedHandler
    : public ICoreWebView2CreateCoreWebView2ControllerCompletedHandler {
public:
    using Func = std::function<HRESULT(HRESULT, ICoreWebView2Controller*)>;
    explicit ControllerCreatedHandler(Func fn) : fn_(std::move(fn)), ref_(1) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown ||
            riid == IID_ICoreWebView2CreateCoreWebView2ControllerCompletedHandler) {
            *ppv = static_cast<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++ref_; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r = --ref_;
        if (r == 0) delete this;
        return r;
    }
    HRESULT STDMETHODCALLTYPE Invoke(HRESULT res, ICoreWebView2Controller* ctrl) override {
        return fn_ ? fn_(res, ctrl) : S_OK;
    }

private:
    Func fn_;
    std::atomic<ULONG> ref_;
};

}  // namespace

#endif

class WebContainerWidget : public QWidget {
public:
    WebContainerWidget(const QString& providerKey, const QString& providerLabel,
                       QWidget* parent = nullptr)
        : QWidget(parent), provider_key_(providerKey), provider_label_(providerLabel) {
        setAttribute(Qt::WA_NativeWindow, true);
        setAttribute(Qt::WA_PaintOnScreen, true);
        setup_ui();
    }

    ~WebContainerWidget() override {
        cleanup();
    }

    void load_url(const QString& url) {
        current_url_ = url;
        pending_html_.clear();
#if defined(CASU_HAVE_WEBVIEW2)
        if (webview_) {
            webview_->Navigate(reinterpret_cast<LPCWSTR>(url.utf16()));
            return;
        }
        pending_url_ = url;
        if (!init_started_) {
            init_webview2();
        } else if (init_failed_) {
            update_fallback();
        }
#else
        update_fallback();
#endif
    }

    void set_html(const QString& html) {
        pending_url_.clear();
        pending_html_ = html;
#if defined(CASU_HAVE_WEBVIEW2)
        if (webview_) {
            webview_->NavigateToString(reinterpret_cast<LPCWSTR>(html.utf16()));
            return;
        }
        if (!init_started_) {
            init_webview2();
        }
#endif
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        QWidget::resizeEvent(event);
#if defined(CASU_HAVE_WEBVIEW2)
        if (controller_) {
            RECT bounds{0, 0, static_cast<LONG>(width()), static_cast<LONG>(height())};
            controller_->put_Bounds(bounds);
        }
#endif
    }

    void showEvent(QShowEvent* event) override {
        QWidget::showEvent(event);
#if defined(CASU_HAVE_WEBVIEW2)
        if (!init_started_) {
            init_webview2();
        }
        if (controller_) {
            controller_->put_IsVisible(TRUE);
        }
#endif
    }

    void hideEvent(QHideEvent* event) override {
        QWidget::hideEvent(event);
#if defined(CASU_HAVE_WEBVIEW2)
        if (controller_) {
            controller_->put_IsVisible(FALSE);
        }
#endif
    }

private:
    void setup_ui() {
        auto* layout = new QVBoxLayout(this);
        layout->setContentsMargins(0, 0, 0, 0);
        layout->setSpacing(0);

        fallback_panel_ = new QWidget(this);
        fallback_panel_->setStyleSheet(
            QStringLiteral(
                "QWidget { background: #121212; border: 1px solid #2a2a2a; border-radius: 8px; margin: 12px; }"
                "QLabel#FallbackTitle { color: #ff1e2d; font-size: 15px; font-weight: bold; border: none; background: transparent; }"
                "QLabel#FallbackText { color: #cccccc; font-size: 12px; border: none; background: transparent; }"
                "QLabel#FallbackUrl { color: #777777; font-size: 11px; font-family: monospace; border: none; background: transparent; }"
                "QPushButton#FallbackBtn { background: #1f1f1f; color: #ffffff; border: 1px solid #444444; border-radius: 4px; padding: 6px 14px; }"
                "QPushButton#FallbackBtn:hover { background: #2f2f2f; border-color: #ff1e2d; }"
                "QPushButton#FallbackPrimary { background: #ff1e2d; color: #ffffff; font-weight: bold; border: 1px solid #ff1e2d; border-radius: 4px; padding: 6px 14px; }"
                "QPushButton#FallbackPrimary:hover { background: #d61523; }"));

        auto* p_layout = new QVBoxLayout(fallback_panel_);
        p_layout->setContentsMargins(20, 20, 20, 20);
        p_layout->setSpacing(12);

        fallback_title_ = new QLabel(
            QStringLiteral("Eingebetteter Web-Player (%1)").arg(provider_label_), fallback_panel_);
        fallback_title_->setObjectName(QStringLiteral("FallbackTitle"));
        p_layout->addWidget(fallback_title_);

        fallback_desc_ = new QLabel(
            QStringLiteral("Der integrierte Player benötigt die Microsoft Edge WebView2 Runtime.\n"
                           "Die DRM-Freigabe erfolgt durch den Anbieter. Bei fehlender Runtime bitte den MPCASU-Installer erneut ausführen."),
            fallback_panel_);
        fallback_desc_->setObjectName(QStringLiteral("FallbackText"));
        fallback_desc_->setWordWrap(true);
        p_layout->addWidget(fallback_desc_);

        fallback_url_ = new QLabel(fallback_panel_);
        fallback_url_->setObjectName(QStringLiteral("FallbackUrl"));
        fallback_url_->setWordWrap(true);
        p_layout->addWidget(fallback_url_);

        auto* btn_layout = new QHBoxLayout();
        btn_layout->setSpacing(10);
        fallback_open_btn_ = new QPushButton(QStringLiteral("Erneut versuchen"), fallback_panel_);
        fallback_open_btn_->setObjectName(QStringLiteral("FallbackPrimary"));
        connect(fallback_open_btn_, &QPushButton::clicked, this, [this]() {
            if (!current_url_.isEmpty()) {
#if defined(CASU_HAVE_WEBVIEW2)
                if (init_failed_) { cleanup(); init_failed_ = false; init_started_ = false; }
#endif
                load_url(current_url_);
            }
        });
        btn_layout->addWidget(fallback_open_btn_);

        fallback_dl_btn_ = nullptr;
        btn_layout->addStretch(1);
        p_layout->addLayout(btn_layout);
        p_layout->addStretch(1);

        layout->addWidget(fallback_panel_);
        fallback_panel_->show();
    }

    void update_fallback() {
        if (fallback_url_) {
            fallback_url_->setText(current_url_.isEmpty()
                                       ? QStringLiteral("Keine URL geladen")
                                       : QStringLiteral("URL: %1").arg(current_url_));
        }
    }

    void cleanup() {
#if defined(CASU_HAVE_WEBVIEW2)
        if (webview_) {
            webview_->Release();
            webview_ = nullptr;
        }
        if (controller_) {
            controller_->Close();
            controller_->Release();
            controller_ = nullptr;
        }
        if (env_) {
            env_->Release();
            env_ = nullptr;
        }
#endif
    }

#if defined(CASU_HAVE_WEBVIEW2)
    void init_webview2() {
        if (init_started_) return;
        init_started_ = true;

        auto createEnvFn = get_create_env_fn();
        if (!createEnvFn) {
            init_failed_ = true;
            update_fallback();
            return;
        }

        QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
        if (base.isEmpty()) base = QDir::tempPath() + QStringLiteral("/casu");
        QString userDataDir = base + QStringLiteral("/webview2/") + provider_key_;
        QDir().mkpath(userDataDir);

        HWND hwnd = reinterpret_cast<HWND>(winId());

        HRESULT hr = createEnvFn(
            nullptr,
            reinterpret_cast<PCWSTR>(userDataDir.utf16()),
            nullptr,
            new EnvironmentCreatedHandler(
                [this, hwnd](HRESULT res, ICoreWebView2Environment* env) -> HRESULT {
                    if (FAILED(res) || !env) {
                        init_failed_ = true;
                        update_fallback();
                        return S_OK;
                    }
                    env_ = env;
                    env_->AddRef();
                    env_->CreateCoreWebView2Controller(
                        hwnd,
                        new ControllerCreatedHandler(
                            [this](HRESULT cRes, ICoreWebView2Controller* ctrl) -> HRESULT {
                                if (FAILED(cRes) || !ctrl) {
                                    init_failed_ = true;
                                    update_fallback();
                                    return S_OK;
                                }
                                controller_ = ctrl;
                                controller_->AddRef();
                                controller_->get_CoreWebView2(&webview_);
                                if (webview_) {
                                    auto* handler = new NewWindowHandler();
                                    EventRegistrationToken token{};
                                    webview_->add_NewWindowRequested(handler, &token);
                                    handler->Release();
                                }
                                controller_->put_IsVisible(TRUE);
                                RECT r{0, 0, static_cast<LONG>(width()), static_cast<LONG>(height())};
                                controller_->put_Bounds(r);

                                if (fallback_panel_) fallback_panel_->hide();

                                if (!pending_url_.isEmpty() && webview_) {
                                    webview_->Navigate(reinterpret_cast<PCWSTR>(pending_url_.utf16()));
                                    pending_url_.clear();
                                } else if (!pending_html_.isEmpty() && webview_) {
                                    webview_->NavigateToString(
                                        reinterpret_cast<PCWSTR>(pending_html_.utf16()));
                                    pending_html_.clear();
                                }
                                return S_OK;
                            }));
                    return S_OK;
                }));

        if (FAILED(hr)) {
            init_failed_ = true;
            update_fallback();
        }
    }
#endif

    QString provider_key_;
    QString provider_label_;
    QString current_url_;
    QString pending_url_;
    QString pending_html_;
    bool init_started_ = false;
    bool init_failed_ = false;

    QWidget* fallback_panel_ = nullptr;
    QLabel* fallback_title_ = nullptr;
    QLabel* fallback_desc_ = nullptr;
    QLabel* fallback_url_ = nullptr;
    QPushButton* fallback_open_btn_ = nullptr;
    QPushButton* fallback_dl_btn_ = nullptr;

#if defined(CASU_HAVE_WEBVIEW2)
    ICoreWebView2Environment* env_ = nullptr;
    ICoreWebView2Controller* controller_ = nullptr;
    ICoreWebView2* webview_ = nullptr;
#endif
};

namespace {

// Persistent profile storage directory for QtWebEngine (Linux/MSVC build).
QString profile_storage_dir() {
#if defined(CASU_HAVE_WEBENGINE)
    const QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (base.isEmpty()) return QString();
    return base + QLatin1String("/webengine");
#else
    return QString();
#endif
}

}  // namespace

WebPlayerTabs::WebPlayerTabs(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("WebPlayers"));
    build_tabs();
}

WebPlayerTabs::~WebPlayerTabs() = default;

void WebPlayerTabs::build_tabs() {
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);
    tabs_ = new QTabWidget(this);
    tabs_->setDocumentMode(true);
    layout->addWidget(tabs_);

#if defined(CASU_HAVE_WEBENGINE)
    auto* profile = new QWebEngineProfile(QStringLiteral("mpcasu"), this);
    profile->setPersistentCookiesPolicy(QWebEngineProfile::ForcePersistentCookies);
    const QString storage = profile_storage_dir();
    if (!storage.isEmpty()) {
        QDir().mkpath(storage);
        profile->setPersistentStoragePath(storage);
    }
    profile->setHttpCacheType(QWebEngineProfile::DiskHttpCache);
#endif

    for (const casu::web::WebPlayerSpec& spec : casu::web::web_players()) {
        const QString key = QString::fromStdString(spec.key);
        const QString label = QString::fromStdString(spec.label);
        QLineEdit* entry = nullptr;
        QWidget* view_widget = nullptr;
        QWidget* page = make_page(key, label, &entry, &view_widget);
        entry->setPlaceholderText(QStringLiteral("%1 URL oder Suchbegriff…").arg(label));
        QObject::connect(entry, &QLineEdit::returnPressed, this, [this, key] { submit(key); });
        entries_[key] = entry;

#if defined(CASU_HAVE_WEBENGINE)
        auto* qwe_view = new QWebEngineView(page);
        qwe_view->setPage(new QWebEnginePage(profile, qwe_view));
        views_[key] = qwe_view;
        qobject_cast<QVBoxLayout*>(page->layout())->addWidget(qwe_view);
#elif defined(CASU_HAVE_WEBVIEW2)
        auto* wv2_view = new WebContainerWidget(key, label, page);
        views_[key] = wv2_view;
        qobject_cast<QVBoxLayout*>(page->layout())->addWidget(wv2_view);
#else
        auto* fallback_view = new WebContainerWidget(key, label, page);
        views_[key] = fallback_view;
        qobject_cast<QVBoxLayout*>(page->layout())->addWidget(fallback_view);
#endif
        tabs_->addTab(page, label);
    }

    // BROWSE tab: a general embedded browser (loads any site directly).
    QLineEdit* browse_entry = nullptr;
    QWidget* browse_view_widget = nullptr;
    QWidget* browse_page = make_page(QStringLiteral("browse"), QStringLiteral("BROWSE"),
                                     &browse_entry, &browse_view_widget);
    browse_entry->setPlaceholderText(QStringLiteral("Browse — URL oder DuckDuckGo-Suche…"));
    QObject::connect(browse_entry, &QLineEdit::returnPressed, this, [this] { submit_browse(); });
    entries_[QStringLiteral("browse")] = browse_entry;

#if defined(CASU_HAVE_WEBENGINE)
    auto* browse_qwe = new QWebEngineView(browse_page);
    browse_qwe->setPage(new QWebEnginePage(profile, browse_qwe));
    views_[QStringLiteral("browse")] = browse_qwe;
    qobject_cast<QVBoxLayout*>(browse_page->layout())->addWidget(browse_qwe);
#elif defined(CASU_HAVE_WEBVIEW2)
    auto* browse_wv2 = new WebContainerWidget(QStringLiteral("browse"), QStringLiteral("BROWSE"),
                                              browse_page);
    views_[QStringLiteral("browse")] = browse_wv2;
    qobject_cast<QVBoxLayout*>(browse_page->layout())->addWidget(browse_wv2);
#else
    auto* browse_fallback = new WebContainerWidget(QStringLiteral("browse"),
                                                   QStringLiteral("BROWSE"), browse_page);
    views_[QStringLiteral("browse")] = browse_fallback;
    qobject_cast<QVBoxLayout*>(browse_page->layout())->addWidget(browse_fallback);
#endif
    tabs_->addTab(browse_page, QStringLiteral("BROWSE"));
}

QWidget* WebPlayerTabs::make_page(const QString& key, const QString& label,
                                 QLineEdit** entry_out, QWidget** view_out) {
    Q_UNUSED(key);
    Q_UNUSED(label);
    Q_UNUSED(view_out);
    auto* page = new QWidget(this);
    page->setStyleSheet(QStringLiteral("background: transparent;"));
    auto* page_layout = new QVBoxLayout(page);
    page_layout->setContentsMargins(6, 6, 6, 6);
    page_layout->setSpacing(6);
    auto* entry = new QLineEdit(page);
    entry->setObjectName(QStringLiteral("IconButton"));
    page_layout->addWidget(entry);
    if (entry_out) *entry_out = entry;
    return page;
}

void WebPlayerTabs::submit(const QString& key) {
    QLineEdit* entry = entries_.value(key);
    if (!entry) return;
    QString text = entry->text().trimmed();
    if (text.isEmpty()) return;
    const bool is_url = text.contains(QLatin1String("://")) && text.contains(QLatin1Char('.'));
    open(key, is_url ? QString() : text, is_url ? text : QString());
}

void WebPlayerTabs::submit_browse() {
    QLineEdit* entry = entries_.value(QStringLiteral("browse"));
    if (!entry) return;
    QString text = entry->text().trimmed();
    if (text.isEmpty()) return;
    QString target;
    if (text.contains(QLatin1String("://")) && text.contains(QLatin1Char('.'))) {
        target = text;
    } else {
        QString q = text;
        q.replace(QLatin1Char(' '), QLatin1String("+"));
        target = QStringLiteral("https://duckduckgo.com/?q=") + q;
    }
    QWidget* view = views_.value(QStringLiteral("browse"));
    if (!view) return;
#if defined(CASU_HAVE_WEBENGINE)
    if (auto* qwe = qobject_cast<QWebEngineView*>(view)) qwe->load(QUrl(target));
#else
    if (auto* wv = static_cast<WebContainerWidget*>(view)) wv->load_url(target);
#endif
}

void WebPlayerTabs::open(const QString& provider, const QString& query,
                         const QString& url) {
    QString key = provider;
    int browse_index = tabs_->count() - 1;
    if (key == QLatin1String("browse")) {
        tabs_->setCurrentIndex(browse_index);
        QString target = url;
        if (target.isEmpty())
            target = query.isEmpty()
                         ? QString::fromStdString(casu::web::browse_url())
                         : QStringLiteral("https://duckduckgo.com/?q=") +
                               QString(query).replace(QLatin1Char(' '), QLatin1String("+"));
        QWidget* view = views_.value(key);
        if (view) {
#if defined(CASU_HAVE_WEBENGINE)
            if (auto* qwe = qobject_cast<QWebEngineView*>(view)) qwe->load(QUrl(target));
#else
            if (auto* wv = static_cast<WebContainerWidget*>(view)) wv->load_url(target);
#endif
        }
        return;
    }

    if (!entries_.contains(key)) key = QStringLiteral("spotify");
    int index = 0;
    const auto specs = casu::web::web_players();
    for (std::size_t i = 0; i < specs.size(); ++i) {
        if (QString::fromStdString(specs[i].key) == key) {
            index = static_cast<int>(i);
            break;
        }
    }
    tabs_->setCurrentIndex(index);
    if (QLineEdit* entry = entries_.value(key)) {
        if (!query.isEmpty()) entry->setText(query);
    }
    const std::string target =
        casu::web::web_player_url(key.toStdString(), query.toStdString(), url.toStdString());
    QWidget* view = views_.value(key);
    if (view) {
#if defined(CASU_HAVE_WEBENGINE)
        if (auto* qwe = qobject_cast<QWebEngineView*>(view))
            qwe->load(QUrl(QString::fromStdString(target)));
#else
        if (auto* wv = static_cast<WebContainerWidget*>(view))
            wv->load_url(QString::fromStdString(target));
#endif
    }
}

bool WebPlayerTabs::play_video(const QString& url, const QString& title) {
    Q_UNUSED(title);
    QString safe = url;
    safe.replace(QLatin1String("&"), QLatin1String("&amp;"));
    safe.replace(QLatin1String("'"), QLatin1String("&#39;"));
    const QString html = QStringLiteral(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;height:100%;background:#000}"
        "video{width:100vw;height:100vh;background:#000;outline:none}</style>"
        "</head><body><video src='%1' autoplay controls playsinline "
        "style='width:100vw;height:100vh'></video></body></html>")
                             .arg(safe);
    QWidget* view = views_.value(QStringLiteral("browse"));
    if (!view) return false;
#if defined(CASU_HAVE_WEBENGINE)
    if (auto* qwe = qobject_cast<QWebEngineView*>(view))
        qwe->setHtml(html, QUrl(QStringLiteral("https://www.youtube.com/")));
#else
    if (auto* wv = static_cast<WebContainerWidget*>(view))
        wv->set_html(html);
#endif
    int idx = tabs_->count() - 1;
    tabs_->setCurrentIndex(idx);
    return true;
}

void WebPlayerTabs::focus_entry(const QString& provider) {
    if (QLineEdit* entry = entries_.value(provider)) {
        entry->setFocus();
        entry->selectAll();
    }
}

}  // namespace mpcasu