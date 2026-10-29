// This device's interface language: the status bar on the main app, the
// header of /engineer. Per device like the speed units — a phone in Spanish
// and a laptop in English can share one logger.

import { Select } from "@/components/ui/Select";
import { setLanguagePreference, useI18n, useT } from "@/i18n";
import {
  LANGUAGES,
  browserLanguages,
  detectLanguage,
  languageName,
  type LanguagePreference,
} from "@/i18n/languages";
import { toast } from "@/store/toasts";

export function LanguagePicker({ className = "" }: { className?: string }) {
  const t = useT();
  const preference = useI18n((s) => s.preference);
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
      className={`h-[22px] px-2 text-[11px] ${className}`}
    />
  );
}
