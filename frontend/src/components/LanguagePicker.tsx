// This device's interface language: the status bar on the main app, the
// header of /engineer. Per device like the speed units — a phone in Spanish
// and a laptop in English can share one logger.

import { Select } from "@/components/ui/Select";
import { setLanguagePreference, useI18n, useLanguage, useT } from "@/i18n";
import {
  LANGUAGES,
  baseOf,
  browserLanguages,
  detectLanguage,
  languageName,
  type LanguagePreference,
} from "@/i18n/languages";
import { toast } from "@/store/toasts";

export function LanguagePicker({ className = "" }: { className?: string }) {
  const t = useT();
  const preference = useI18n((s) => s.preference);
  const language = useLanguage();
  const detected = languageName(detectLanguage(browserLanguages()));

  return (
    <Select
      ariaLabel={t("lang.label")}
      value={preference}
      onValueChange={(value) => {
        setLanguagePreference(value as LanguagePreference).catch(() =>
          toast(t("lang.loadFailed"), "error"),
        );
      }}
      options={[
        { value: "auto", label: t("lang.auto", { language: detected }) },
        // Each language in its own name: someone looking for theirs reads it.
        ...LANGUAGES.map((l) => ({ value: l.code, label: l.name })),
      ]}
      // The bar has room for a code, not "Português (Brasil)"; the list
      // keeps each language's full name.
      display={
        <span className="inline-flex items-center gap-1">
          <GlobeIcon />
          {baseOf(language).toUpperCase()}
        </span>
      }
      className={`h-[22px] px-2 text-[11px] ${className}`}
    />
  );
}

function GlobeIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-3 w-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      aria-hidden
    >
      <circle cx="8" cy="8" r="6.3" />
      <ellipse cx="8" cy="8" rx="2.7" ry="6.3" />
      <path d="M1.7 8h12.6" />
    </svg>
  );
}
