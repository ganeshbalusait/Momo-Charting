import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { SCANNER_SETUP_CHOICES, scannerSetupBadge } from './scannerSetupOptions.js';
import { selectedSetupRules, withSetupRules, strategyRules } from './momxFilters.js';

export default function SetupColumnPicker({ config, direction, count, anchor, onChange, onClose, onSort, enabled }) {
  const selected = selectedSetupRules(config);
  const closeButton = useRef(null);
  useEffect(() => {
    closeButton.current?.focus();
    const close = event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', close, true);
    return () => window.removeEventListener('keydown', close, true);
  }, [onClose]);
  const width = Math.min(420, window.innerWidth - 16);
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8));
  const top = Math.max(8, Math.min(anchor.bottom + 6, window.innerHeight - 460));
  const update = rules => onChange(withSetupRules(config, rules));
  return createPortal(<div className="momx-setup-picker-overlay" onClick={onClose}>
    <section className="momx-setup-picker" style={{ left, top, width, maxHeight: window.innerHeight - top - 8 }} role="dialog" aria-modal="true" aria-label="Setup column rules" onClick={event => event.stopPropagation()}>
      <header><strong>Setup · {enabled ? 'ON' : 'OFF'} · {selected.length} of {SCANNER_SETUP_CHOICES.length} selected · {count} matches</strong><button ref={closeButton} type="button" aria-label="Close Setup rules" onClick={onClose}>×</button></header>
      <div className="momx-setup-picker-body">
        <div className="momx-setup-rules-controls">
          <label>Match <select aria-label="Setup rules match mode" value={config.setupRulesMode === 'any' ? 'any' : 'all'} onChange={event => onChange({ ...config, setupRulesMode: event.target.value })}>
            <option value="all">All selected rules</option><option value="any">Any selected rule</option>
          </select></label>
          <label><input type="checkbox" aria-label="Select all Setup rules" checked={selected.length === SCANNER_SETUP_CHOICES.length} onChange={event => update(event.target.checked ? SCANNER_SETUP_CHOICES.map(([key]) => key) : [])} />Select all {SCANNER_SETUP_CHOICES.length}</label>
        </div>
        <div className="momx-setup-rule-grid">{SCANNER_SETUP_CHOICES.map(([key]) => {
          const badge = scannerSetupBadge(key, direction);
          return <label key={key} title={key === 'aPlus' ? 'Only tickers displaying the exact A+ grade in Setup. Separate from MX A+.' : strategyRules(direction)[key]}>
          <input type="checkbox" checked={selected.includes(key)} onChange={event => update(event.target.checked ? [...selected, key] : selected.filter(value => value !== key))} />
          <span className={badge.className} data-setup-tag={badge.tag}>{badge.text}</span>
        </label>; })}</div>
        <p>Selections apply immediately to the same ticker. All requires every checked rule; Any requires at least one. No default choices. A+ is the displayed grade, separate from MX A+. MOM is the visible strong/filled tag. SECTOR is bull-only. Selecting both Explosive and Steady under All returns no matches.</p>
        <p>These choices replace the old single-Setup strategy, including AI score 55%+. Other filter settings are kept on standby until the selection is cleared.</p>
      </div>
      <footer><button type="button" onClick={() => update([])} disabled={!selected.length}>Clear selections</button><button type="button" onClick={onSort}>Sort Setup</button><button type="button" onClick={onClose}>Done</button></footer>
    </section>
  </div>, document.body);
}
