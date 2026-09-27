// Shared shell for the route-level error / empty pages (1a–1e): a marshal's
// flag and kicker, a big code, a headline, one paragraph, the page's own
// controls, two actions and a diagnostic line — with an illustration beside
// it on wide screens. Renders inside the normal app shell, so the StatusBar
// stays and the driver can still switch views.

export interface ErrorAction {
  label: string;
  onClick?: () => void;
  href?: string; // external links open in a new tab
  disabled?: boolean;
}

export interface ErrorPageProps {
  /** CSS background of the 28×20 flag swatch. */
  flag: string;
  kicker: string;
  code: string;
  title: string;
  body: React.ReactNode;
  primary: ErrorAction;
  secondary?: ErrorAction;
  diag?: string;
  /** Page-specific controls between the body and the actions. */
  children?: React.ReactNode;
  /** Dropped below ~800px, where the page is a single column. */
  art?: React.ReactNode;
  /** The wider column: "art" = 5fr text / 7fr art (default), "text" = 7fr / 5fr. */
  wide?: "text" | "art";
  /** Single centred column with hazard stripes (the unreachable page). */
  hazard?: boolean;
}

export function ErrorPage({
  flag,
  kicker,
  code,
  title,
  body,
  primary,
  secondary,
  diag,
  children,
  art,
  wide = "art",
  hazard = false,
}: ErrorPageProps) {
  const centred = hazard || !art;
  const cols = !art
    ? ""
    : wide === "text"
      ? "min-[800px]:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]"
      : "min-[800px]:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]";
  return (
    // Bleeds over <main>'s padding (px-5 pt-3.5 pb-7) so the page fills the
    // space under the StatusBar and the hazard stripes run edge to edge.
    <div className="-mx-5 -mb-7 -mt-3.5 flex min-h-[calc(100%+42px)] flex-col">
      {hazard && <div className="hazard-stripes animate-crawl" />}
      <div
        className={`grid flex-1 items-center gap-10 px-5 py-10 sm:px-14 ${cols}`}
      >
        <div
          className={`flex flex-col gap-5 ${centred ? "items-center text-center" : "items-start text-left"}`}
        >
          <div className="flex items-center gap-2.5">
            <span
              className="h-5 w-7 shrink-0 rounded-[2px] shadow-[inset_0_0_0_1px_var(--color-edge-bright)]"
              style={{ background: flag }}
            />
            <span className="section-header">{kicker}</span>
          </div>
          <div className="font-tabular text-[80px] font-medium leading-[0.9] tracking-[-0.04em] text-ink-soft sm:text-[120px]">
            {code}
          </div>
          <h1 className="m-0 max-w-[520px] text-[26px] font-medium leading-[1.1] text-balance sm:text-[32px]">
            {title}
          </h1>
          <p className="m-0 max-w-[460px] text-[15px] leading-normal text-pretty text-ink-dim">
            {body}
          </p>
          {children}
          <div className="flex flex-wrap gap-2">
            <ActionButton action={primary} primary />
            {secondary && <ActionButton action={secondary} />}
          </div>
          {diag && (
            <span className="break-all font-mono text-xs text-ink-ghost">{diag}</span>
          )}
        </div>
        {art && (
          <div className="hidden h-full min-w-0 items-center justify-center min-[800px]:flex">
            {art}
          </div>
        )}
      </div>
      {hazard && <div className="hazard-stripes animate-crawl" />}
    </div>
  );
}

function ActionButton({ action, primary }: { action: ErrorAction; primary?: boolean }) {
  const cls = `btn ${primary ? "btn-primary" : ""} px-3.5 py-1.5 text-xs`;
  if (action.href) {
    return (
      <a className={`${cls} no-underline`} href={action.href} target="_blank" rel="noreferrer">
        {action.label}
      </a>
    );
  }
  return (
    <button className={cls} onClick={action.onClick} disabled={action.disabled}>
      {action.label}
    </button>
  );
}

// The docs pages the error pages point at.
const DOCS = "https://jbhoorasingh.github.io/gt7-datalogger";
export const TROUBLESHOOTING_URL = `${DOCS}/reference/troubleshooting/`;
export const ADMIN_TOKEN_DOCS_URL = `${TROUBLESHOOTING_URL}#401-403-admin-token-errors`;
export const NO_TELEMETRY_DOCS_URL = `${TROUBLESHOOTING_URL}#server-up-no-telemetry-amber-status-dot`;
