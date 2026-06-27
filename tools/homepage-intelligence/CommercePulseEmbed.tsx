"use client";

/**
 * Live Agentic Commerce Pulse embed — consumes GET /embed/commerce-pulse from the API.
 * Copy to: landing/components/CommercePulseEmbed.tsx
 */

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL || "https://cli-market-production.up.railway.app"
).replace(/\/$/, "");

type Props = {
  country?: string;
  lang?: "es" | "en";
  className?: string;
};

export default function CommercePulseEmbed({
  country = "PE",
  lang = "es",
  className = "",
}: Props) {
  const src = `${API_BASE}/embed/commerce-pulse?country=${encodeURIComponent(country)}&lang=${lang}`;
  const fullReport = `${API_BASE}/intelligence?country=${encodeURIComponent(country)}&lang=${lang}`;
  const isES = lang === "es";

  return (
    <div className={`mt-10 ${className}`.trim()}>
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3 mb-4">
        <div>
          <p className="text-xs font-mono uppercase tracking-widest text-[#00ff88]/80 mb-1">
            {isES ? "EN VIVO" : "LIVE"}
          </p>
          <h3 className="text-lg sm:text-xl font-mono text-[var(--cm-on-surface)]">
            {isES ? "This Week in LatAm Commerce" : "This Week in LatAm Commerce"}
          </h3>
          <p className="text-sm text-[var(--cm-on-surface-variant)] mt-1">
            {isES
              ? "Agentic Commerce Pulse — señales de góndola actualizadas cada 4h."
              : "Agentic Commerce Pulse — shelf signals refreshed every 4h."}
          </p>
        </div>
        <a
          href={fullReport}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-mono text-[#00ff88] hover:underline shrink-0"
        >
          {isES ? "Reporte completo →" : "Full report →"}
        </a>
      </div>
      <iframe
        src={src}
        title={isES ? "CLI Market Agentic Commerce Pulse" : "CLI Market Agentic Commerce Pulse"}
        className="w-full min-h-[520px] rounded-xl border border-[#1e2a22] bg-[#0a0c0f]"
        loading="lazy"
        referrerPolicy="no-referrer-when-downgrade"
      />
      <p className="mt-3 text-xs text-[var(--cm-on-surface-variant)] font-mono">
        <code className="text-[#00ff88]/90">market pulse -c {country}</code>
        {" · "}
        <code className="text-[#00ff88]/90">market forecast leche</code>
        {" · "}
        <code className="text-[#00ff88]/90">market arbitrage arroz</code>
      </p>
    </div>
  );
}
