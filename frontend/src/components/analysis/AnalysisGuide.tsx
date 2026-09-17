// The Analysis view's guide: what each feature and each chart channel is, in
// a sentence or two, with a link to the documentation for the rest. Opened
// from the toolbar's "?" (or the channel picker), never on its own.

import { useEffect, useMemo, useState } from "react";
import { LargeDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import {
  channelDocs,
  DOCS_SITE,
  docsUrl,
  GUIDE_FEATURES,
  matchesQuery,
} from "@/lib/analysisGuide";
import { CHANNEL_GROUPS, CHANNELS } from "@/lib/channels";

export type GuideTab = "features" | "channels";

export function AnalysisGuide({
  open,
  tab,
  onTabChange,
  charted,
  onClose,
}: {
  open: boolean;
  tab: GuideTab;
  onTabChange: (tab: GuideTab) => void;
  /** Channel keys currently on the chart stack, marked in the list. */
  charted: string[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  // A fresh search each time the guide opens.
  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const features = useMemo(
    () => GUIDE_FEATURES.filter((f) => matchesQuery(query, f.title, f.body)),
    [query],
  );
  const channels = useMemo(
    () => CHANNELS.filter((c) => matchesQuery(query, c.title, c.description, c.needs, c.group)),
    [query],
  );
  const shown = tab === "features" ? features.length : channels.length;
  const other: GuideTab = tab === "features" ? "channels" : "features";
  const otherCount = tab === "features" ? channels.length : features.length;

  return (
    <LargeDialog open={open} title="Analysis guide" size="medium" onClose={onClose}>
      <div className="flex h-full flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-edge px-3 py-2">
          <SegmentedControl
            ariaLabel="Guide section"
            size="sm"
            value={tab}
            onValueChange={onTabChange}
            options={[
              { value: "features", label: `Features · ${features.length}` },
              { value: "channels", label: `Channels · ${channels.length}` },
            ]}
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search, e.g. understeer, lockup, sync"
            aria-label="Search the guide"
            className="min-w-40 flex-1 rounded-[5px] border border-edge bg-transparent px-2.5 py-[3px] text-[11.5px] outline-none placeholder:text-ink-ghost focus:border-accent"
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          {shown === 0 && (
            <div className="py-8 text-center text-xs text-ink-dim">
              Nothing here matches “{query.trim()}”.
              {otherCount > 0 && (
                <button
                  className="ml-1 text-accent hover:text-accent-300"
                  onClick={() => onTabChange(other)}
                >
                  {otherCount} in {other === "features" ? "Features" : "Channels"}
                </button>
              )}
            </div>
          )}

          {tab === "features" &&
            features.map((f) => (
              <Entry key={f.title} title={f.title} docs={f.docs}>
                {f.body}
              </Entry>
            ))}

          {tab === "channels" &&
            CHANNEL_GROUPS.map((group) => {
              const items = channels.filter((c) => c.group === group);
              if (items.length === 0) return null;
              return (
                <section key={group} className="pb-1 pt-2">
                  <h3 className="section-header">{group}</h3>
                  {items.map((c) => (
                    <Entry
                      key={c.key}
                      title={c.title}
                      docs={channelDocs(c)}
                      badges={
                        <>
                          {charted.includes(c.key) && (
                            <span className="rounded-[9px] bg-accent/15 px-1.5 text-[9.5px] text-accent-300">
                              charted
                            </span>
                          )}
                          {c.needs && (
                            <span className="rounded-[9px] border border-edge px-1.5 text-[9.5px] text-ink-faint">
                              {c.needs}
                            </span>
                          )}
                        </>
                      }
                    >
                      {c.description}
                    </Entry>
                  ))}
                </section>
              );
            })}
        </div>

        <div className="border-t border-edge px-4 py-2 text-[10.5px] text-ink-faint">
          Every entry links to the{" "}
          <a
            className="text-ink-dim underline-offset-2 hover:text-accent hover:underline"
            href={DOCS_SITE}
            target="_blank"
            rel="noreferrer"
          >
            documentation
          </a>{" "}
          for the details.
        </div>
      </div>
    </LargeDialog>
  );
}

function Entry({
  title,
  docs,
  badges,
  children,
}: {
  title: string;
  docs: string;
  badges?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <article className="border-b border-divider py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h4 className="text-[12.5px] font-medium text-ink">{title}</h4>
        {badges}
        <a
          className="ml-auto shrink-0 text-[10.5px] text-ink-dim transition-colors hover:text-accent"
          href={docsUrl(docs)}
          target="_blank"
          rel="noreferrer"
          aria-label={`${title} in the documentation`}
        >
          docs ↗
        </a>
      </div>
      <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-muted">{children}</p>
    </article>
  );
}
