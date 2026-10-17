import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Clipboard,
  FileCode,
  HardDrives,
  List,
  Pause,
  Play,
  X,
} from "@phosphor-icons/react";

const quickStart = `git clone https://github.com/runestra-app/runestra.git\ncd runestra\nbash scripts/quick-start.sh`;

function trackEvent(name, placement) {
  window.umami?.track(name, { placement });
}

const productSlides = [
  {
    src: "/brand/product-run.png",
    alt: "Runestra showing the local-actions project and a successful commands workflow run.",
    title: "A successful workflow run",
    detail: "The engine overview shows the project, workflow, and completed run.",
  },
  {
    src: "/brand/product-run-detail.png",
    alt: "Run detail showing two successful steps, their dependency, logs, and run activity.",
    title: "Inspect each run",
    detail: "Follow step state, dependencies, logs, and activity from one run detail.",
  },
  {
    src: "/brand/product-workflows.png",
    alt: "Runestra workflow list showing the commands, slow, and failure workflows.",
    title: "Workflows stay visible",
    detail: "Review the workflows configured for a project and open their runs.",
  },
];

function ProductGallery() {
  const galleryRef = useRef(null);
  const pointerInteraction = useRef(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [isVisible, setIsVisible] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isPageVisible, setIsPageVisible] = useState(true);
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    const gallery = galleryRef.current;
    if (!gallery) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => setIsVisible(entry.isIntersecting),
      { threshold: 0.25 },
    );
    observer.observe(gallery);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setReduceMotion(preference.matches);
    const updatePageVisibility = () => setIsPageVisible(!document.hidden);

    updatePreference();
    updatePageVisibility();
    preference.addEventListener("change", updatePreference);
    document.addEventListener("visibilitychange", updatePageVisibility);
    return () => {
      preference.removeEventListener("change", updatePreference);
      document.removeEventListener("visibilitychange", updatePageVisibility);
    };
  }, []);

  useEffect(() => {
    if (!isPlaying || !isVisible || isHovered || !isPageVisible || reduceMotion) return undefined;
    const timer = window.setInterval(() => {
      setActiveIndex((index) => (index + 1) % productSlides.length);
    }, 7000);
    return () => window.clearInterval(timer);
  }, [isHovered, isPageVisible, isPlaying, isVisible, reduceMotion]);

  function selectSlide(index) {
    setActiveIndex(index);
    setIsPlaying(false);
  }

  const activeSlide = productSlides[activeIndex];

  return (
    <figure className="product-gallery" ref={galleryRef} onMouseEnter={() => setIsHovered(true)} onMouseLeave={() => setIsHovered(false)} onFocusCapture={() => setIsPlaying(false)}>
      <div className="gallery-frame" role="region" aria-roledescription="carousel" aria-label="Runestra engine screenshots" onPointerDownCapture={() => { pointerInteraction.current = true; }} onPointerUpCapture={() => { pointerInteraction.current = false; }} onPointerCancelCapture={() => { pointerInteraction.current = false; }} onFocusCapture={() => { if (!pointerInteraction.current) setIsPlaying(false); }}>
        {productSlides.map((slide, index) => (
          <a
            className={`gallery-slide${index === activeIndex ? " is-active" : ""}`}
            href={slide.src}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open full-size screenshot: ${slide.title}`}
            aria-hidden={index !== activeIndex}
            tabIndex={index === activeIndex ? 0 : -1}
            key={slide.src}
          >
            <img src={slide.src} alt={index === activeIndex ? slide.alt : ""} width="1280" height="700" loading={index === 0 ? "eager" : "lazy"} />
          </a>
        ))}
        <span className="gallery-count" aria-hidden="true">{String(activeIndex + 1).padStart(2, "0")} / {String(productSlides.length).padStart(2, "0")}</span>
      </div>
      <div className="gallery-caption" aria-live={isPlaying ? "off" : "polite"} aria-atomic="true">
        <div><h3>{activeSlide.title}</h3><p>{activeSlide.detail}</p></div>
        <div className="gallery-controls" aria-label="Screenshot controls">
          <button type="button" aria-label="Previous screenshot" onClick={() => selectSlide((activeIndex + productSlides.length - 1) % productSlides.length)}><ArrowLeft size={17} /></button>
          <button type="button" aria-label={isPlaying ? "Pause automatic rotation" : "Resume automatic rotation"} aria-pressed={!isPlaying} onClick={() => setIsPlaying((playing) => !playing)}>{isPlaying ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}</button>
          <button type="button" aria-label="Next screenshot" onClick={() => selectSlide((activeIndex + 1) % productSlides.length)}><ArrowRight size={17} /></button>
        </div>
      </div>
      <div className="gallery-dots" role="group" aria-label="Choose screenshot">
        {productSlides.map((slide, index) => (
          <button type="button" className={index === activeIndex ? "is-active" : ""} aria-label={`Show screenshot ${index + 1}: ${slide.title}`} aria-current={index === activeIndex ? "true" : undefined} onClick={() => selectSlide(index)} key={slide.src} />
        ))}
      </div>
      <figcaption>Actual local engine interface, shown in dark mode. <a href={activeSlide.src} target="_blank" rel="noreferrer">Open full-size image</a>.</figcaption>
    </figure>
  );
}

function Brand({ compact = false }) {
  return (
    <a className={`brand ${compact ? "brand-compact" : ""}`} href="#top" aria-label="Runestra home">
      <img src="/brand/runestra-mark-mint.svg" alt="" />
      <span>Runestra</span>
    </a>
  );
}

function PrivacyPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = "Privacy notice | Runestra";
    let description = document.querySelector('meta[name="description"]');
    if (!description) {
      description = document.createElement("meta");
      description.name = "description";
      document.head.appendChild(description);
    }
    const previousDescription = description.content;
    description.content = "How Runestra's public website uses Umami analytics and processes website visits.";
    return () => {
      document.title = previousTitle;
      description.content = previousDescription;
    };
  }, []);

  return (
    <div id="top" className="site-shell">
      <header className="site-header">
        <div className="header-inner"><Brand /><a className="button button-small button-quiet" href="/">Back to home</a></div>
      </header>
      <main className="privacy-page content-width">
        <p className="eyebrow"><span className="eyebrow-line" /> RUNESTRA.APP</p>
        <h1>Privacy notice</h1>
        <p className="privacy-updated">Last updated: 12 October 2026</p>
        <p>This notice explains how the public Runestra website uses privacy-focused analytics. It does not cover the separate Runestra software that you may install and run yourself.</p>

        <h2>Who is responsible</h2>
        <p>The website operator and data controller is <strong>Sašo Sečnjak</strong>, a private individual based in <strong>Slovenia</strong>. For privacy questions or requests, contact <a href="mailto:info@runestra.app">info@runestra.app</a>.</p>

        <h2>What the site measures</h2>
        <p>Runestra uses Umami Cloud to understand aggregate website visits and whether visitors use links such as GitHub, documentation, and the quick start. The tracker is enabled only on <code>runestra.app</code> and <code>www.runestra.app</code>. Local development and review previews are excluded.</p>
        <p>Depending on the visit, analytics may include pages viewed, referring page and campaign parameters, approximate country, browser, operating system, device type, and named link or quick-start events. Event properties describe only the link placement. The tracker is not configured to send names, email addresses, copied commands, or other visitor-entered content.</p>
        <p>Umami says its tracker does not use cookies or store raw IP addresses. It uses the request IP to derive location and anonymous session metrics. See <a href="https://docs.umami.is/docs/faq" target="_blank" rel="noreferrer">Umami's data and privacy FAQ</a>.</p>

        <h2>Purpose and legal basis</h2>
        <p>The purpose is to understand how the public site is used and improve its information and navigation. The controller relies on legitimate interests in maintaining and improving the site. You can object to this processing using the contact details above.</p>

        <h2>Service provider and location</h2>
        <p>Analytics are processed by Umami Cloud. The Runestra Umami website is configured in the EU region. Umami Cloud's current Hobby plan lists six months of analytics data retention; the retention period depends on the account's actual plan. See <a href="https://umami.is/pricing" target="_blank" rel="noreferrer">Umami Cloud plans</a>.</p>
        <p>The site is hosted on Cloudflare Pages. Cloudflare processes request information, which may include IP address, request URL, and technical metadata, to deliver and protect the site. Cloudflare documents that the locations used for processing and storing traffic metadata depend on the account's data localization configuration. The site does not currently claim that Cloudflare traffic metadata is restricted to the EU. See <a href="https://developers.cloudflare.com/data-localization/how-to/pages/" target="_blank" rel="noreferrer">Cloudflare's Pages data localization guidance</a> and <a href="https://www.cloudflare.com/trust-hub/gdpr/" target="_blank" rel="noreferrer">Cloudflare's GDPR information</a>.</p>

        <h2>Your choices and rights</h2>
        <p>You can block analytics requests with browser privacy settings or an extension. Under applicable data protection law, you may request access, correction, deletion, restriction, or portability where applicable, and object to processing by contacting the controller above. You may lodge a complaint with the Slovenian Information Commissioner (<a href="https://www.ip-rs.si/" target="_blank" rel="noreferrer">Informacijski pooblaščenec</a>).</p>

        <h2>Changes</h2>
        <p>This notice will be updated if the analytics or hosting setup changes.</p>
      </main>
      <footer className="site-footer"><div className="footer-inner content-width"><Brand compact /><nav aria-label="Footer navigation"><a href="/">Home</a><a href="https://github.com/runestra-app/runestra">GitHub</a></nav><p>Created by Sašo Sečnjak</p></div></footer>
    </div>
  );
}

function App() {
  const [copied, setCopied] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!["runestra.app", "www.runestra.app"].includes(window.location.hostname)) return undefined;

    const script = document.createElement('script');
    script.defer = true;
    script.src = 'https://cloud.umami.is/script.js';
    script.dataset.websiteId = '00740cd4-d812-4a2d-8b0c-09f39e19fadf';
    document.head.appendChild(script);
    return () => script.remove();
  }, []);

  async function copyQuickStart() {
    try {
      await navigator.clipboard.writeText(quickStart);
      setCopied(true);
      trackEvent("Quick start copied", "Quick start");
      window.setTimeout(() => setCopied(false), 2200);
    } catch {
      setCopied(false);
    }
  }

  function closeMenu() {
    setMenuOpen(false);
  }

  if (window.location.pathname === "/privacy") return <PrivacyPage />;

  return (
    <div id="top" className="site-shell">
      <header className="site-header">
        <div className="header-inner">
          <Brand />
          <button
            className="menu-toggle"
            type="button"
            aria-expanded={menuOpen}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X size={22} /> : <List size={22} />}
          </button>
          <nav className={`main-nav ${menuOpen ? "is-open" : ""}`} aria-label="Main navigation">
            <a href="https://github.com/runestra-app/runestra" onClick={() => { trackEvent("GitHub link click", "Navigation"); closeMenu(); }}>GitHub</a>
            <a href="https://github.com/runestra-app/runestra/tree/main/docs" onClick={() => { trackEvent("Documentation click", "Navigation"); closeMenu(); }}>Docs</a>
            <a className="button button-small button-primary" href="#quick-start" onClick={() => { trackEvent("Get started click", "Navigation"); closeMenu(); }}>Get started</a>
          </nav>
        </div>
      </header>

      <main>
        <section className="hero content-width" aria-labelledby="hero-title">
          <p className="eyebrow"><span className="eyebrow-line" /> OPEN SOURCE · BUILT FOR ENGINEERS</p>
          <h1 id="hero-title">Your repository defines the workflow.<br className="desktop-break" /> Your machines run it.</h1>
          <p className="hero-copy">Run AI agents and shell checks through a central engine and runners you control.</p>
          <div className="hero-actions">
            <a className="button button-primary" href="#quick-start" onClick={() => trackEvent("Get started click", "Hero")}>Get started <ArrowRight size={18} weight="bold" /></a>
            <a className="button button-quiet" href="https://github.com/runestra-app/runestra" onClick={() => trackEvent("GitHub link click", "Hero")}><img className="github-invertocat" src="/brand/github-invertocat-white.svg" alt="" /> View on GitHub</a>
          </div>
        </section>

        <section className="workflow-band" id="how-it-works" aria-label="How Runestra works">
          <div className="workflow-grid content-width">
            <article className="workflow-step">
              <div className="step-icon"><FileCode size={34} weight="light" /></div>
              <p className="step-index">01 / DEFINE</p>
              <h2>Workflow in Git</h2>
              <p>Define the steps as code and keep them with your repository.</p>
            </article>
            <div className="step-connector" aria-hidden="true"><span /></div>
            <article className="workflow-step">
              <div className="step-icon step-icon-mark"><img src="/brand/runestra-mark-mint.svg" alt="" /></div>
              <p className="step-index">02 / COORDINATE</p>
              <h2>Runestra engine</h2>
              <p>Coordinates runs, manages state, and routes work.</p>
            </article>
            <div className="step-connector" aria-hidden="true"><span /></div>
            <article className="workflow-step">
              <div className="step-icon"><HardDrives size={34} weight="light" /></div>
              <p className="step-index">03 / EXECUTE</p>
              <h2>Your runner</h2>
              <p>Executes workflow steps on machines you control.</p>
            </article>
          </div>
        </section>

        <section className="proof-section content-width" aria-labelledby="proof-title">
          <div className="section-heading">
            <p className="eyebrow">A REAL LOCAL RUN</p>
            <h2 id="proof-title">Real workflow, real output.</h2>
            <p>A local project running a workflow, with a successful run. Start with a shell sample before connecting an AI agent.</p>
          </div>
          <ProductGallery />
        </section>

        <section className="quick-section" id="quick-start" aria-labelledby="quick-title">
          <div className="quick-grid content-width">
            <div className="quick-intro">
              <p className="eyebrow">QUICK START</p>
              <h2 id="quick-title">Try a workflow on your machine.</h2>
              <p>On macOS or Linux, Git and Bash are the only prerequisites. The script installs what it needs, starts the engine and runner, then runs a sample.</p>
              <a className="text-link" href="https://github.com/runestra-app/runestra/blob/main/docs/getting-started.md" onClick={() => trackEvent("Documentation click", "Quick start")}>Read the getting started guide <ArrowRight size={16} /></a>
            </div>
            <div className="code-panel">
              <div className="code-panel-head"><span>TERMINAL</span><button className="copy-button" type="button" onClick={copyQuickStart} aria-label="Copy quick start commands" aria-live="polite">{copied ? <Check size={17} /> : <Clipboard size={17} />}{copied ? "Copied" : "Copy"}</button></div>
              <pre><code><span className="code-line"><i>1</i><b>git clone</b> https://github.com/runestra-app/runestra.git</span><span className="code-line"><i>2</i><b>cd</b> runestra</span><span className="code-line"><i>3</i><b>bash</b> scripts/quick-start.sh</span></code></pre>
              <p className="code-note">No Docker or AI account needed for the first shell sample.</p>
            </div>
          </div>
        </section>

        <section className="principles-section content-width" aria-labelledby="principles-title">
          <p className="eyebrow">BUILT FOR ENGINEERS</p>
          <h2 id="principles-title">Small pieces. Clear control.</h2>
          <div className="principles-grid">
            <article><span className="principle-rule" /><h3>Open source</h3><p>Start from source, adapt workflows to your environment, and run them your way.</p><a href="https://github.com/runestra-app/runestra/blob/main/LICENSE">Apache-2.0 license <ArrowRight size={15} /></a></article>
            <article><span className="principle-rule" /><h3>Your machines</h3><p>Use your own infrastructure and runners. Keep code and execution under your control.</p></article>
            <article><span className="principle-rule" /><h3>From checks to agents</h3><p>Run shell checks and AI agents in the same workflow, defined in your repository.</p><a href="https://github.com/runestra-app/runestra/blob/main/docs/getting-started.md#try-an-ai-workflow">Explore an AI workflow <ArrowRight size={15} /></a></article>
          </div>
          <p className="release-note">The quick start runs current source from <code>main</code>. A tagged public release and clean-install checks are still in progress.</p>
        </section>

        <section className="faq-section" aria-labelledby="faq-title">
          <div className="content-width faq-inner">
            <div><p className="eyebrow">BEFORE YOU START</p><h2 id="faq-title">A few practical details.</h2></div>
            <div className="faq-list">
              <details><summary>What do I need for the first run?</summary><p>Git and Bash on macOS or Linux. The local shell sample does not need Docker, an email service, an AI account, or an API key.</p></details>
              <details><summary>Where does workflow execution happen?</summary><p>The engine manages workflow state and routes work. A runner connects to it and executes the workflow steps on a machine you control.</p></details>
              <details><summary>Does using my own runner make untrusted code safe?</summary><p>No. A runner executes code with the access of its operating-system account. Follow the <a href="https://github.com/runestra-app/runestra/blob/main/SECURITY.md">security guidance</a> and isolate untrusted projects.</p></details>
              <details><summary>Can I use an AI agent?</summary><p>Yes. The shell sample is a useful first check. An AI workflow needs a supported agent CLI installed and signed in on the runner machine. <a href="https://github.com/runestra-app/runestra/blob/main/docs/getting-started.md#try-an-ai-workflow">See the AI workflow guide</a>.</p></details>
            </div>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <div className="footer-inner content-width"><Brand compact /><nav aria-label="Footer navigation"><a href="https://github.com/runestra-app/runestra" onClick={() => trackEvent("GitHub link click", "Footer")}>GitHub</a><a href="https://github.com/runestra-app/runestra/tree/main/docs" onClick={() => trackEvent("Documentation click", "Footer")}>Documentation</a><a href="https://github.com/runestra-app/runestra/blob/main/LICENSE">Apache-2.0</a><a href="/privacy">Privacy</a></nav><p>Created by Sašo Sečnjak</p></div>
      </footer>
    </div>
  );
}

export default App;
