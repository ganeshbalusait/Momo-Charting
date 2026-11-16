// The chat window on every page (2026-11-11): his "instead of adding only BOT
// page, why dont add it chart/option like how other chat window in other site
// works". A round button at the bottom right opens the same chat as the Bot
// page's Ask tab. It only reads; it can never place, change or cancel a trade.
// The owner gets the whole chat; anyone else gets market data only.
//
// All chat state lives in here, so typing never re-renders App. App hands over
// plain strings and one boolean, and memo stops App's own re-renders at this door.
import { memo, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { MessageCircle } from "lucide-react";
import BotChat from "./BotChat.jsx";
import { OPEN_LINK_EVENT, cleanLink, linkAllowed } from "./appLinks.js";
import { chatDockTitle, chatTakesEsc, pageChatShowing, subscribePageChat } from "./botChat.js";

const PHONE_WIDTH = "(max-width: 760px)";

function ChatDock({ pageTicker = "", pageName = "", isOwner = false }) {
  const [open, setOpen] = useState(false);
  // The chat is built the first time it is opened and then stays, so closing the
  // window or changing page keeps the conversation.
  const [started, setStarted] = useState(false);
  const button = useRef(null);
  const panel = useRef(null);
  const askTabShowing = useSyncExternalStore(subscribePageChat, pageChatShowing, pageChatShowing);
  const showing = open && !askTabShowing;

  const show = () => { setStarted(true); setOpen(true); };
  const close = () => { setOpen(false); button.current?.focus({ preventScroll: true }); };

  useEffect(() => {
    if (!showing) return undefined;
    // Caught first, so Esc closes this window and does not also shrink a maximized chart behind it.
    const onKey = (event) => {
      // Something on top of the window (the full-screen option chain) or an open menu keeps its own Esc.
      if (event.key !== "Escape" || !chatTakesEsc(panel.current, document)) return;
      event.stopPropagation();
      close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [showing]);

  // On a phone the chat starts just under the app's top bar, which sits above it. A full-screen chart covers that bar, so there it starts at the top.
  useLayoutEffect(() => {
    if (!showing || !window.matchMedia?.(PHONE_WIDTH).matches) return;
    const bar = document.body.classList.contains("oi-chart-card-maximized") ? null : document.querySelector(".topbar.trading-header");
    const bottom = bar ? bar.getBoundingClientRect().bottom : 0;
    document.documentElement.style.setProperty("--chat-dock-top", `${Math.max(0, Math.min(120, Math.round(bottom)))}px`);
  }, [showing]);

  // On a phone the chat fills the screen, so following a link closes it and shows the page. A desktop keeps it beside the page.
  // A link the app will not follow for this person (an owner page) moves nothing, so the chat stays open.
  useEffect(() => {
    const onLink = (event) => {
      if (window.matchMedia?.(PHONE_WIDTH).matches && linkAllowed(cleanLink(event?.detail), isOwner)) setOpen(false);
    };
    window.addEventListener(OPEN_LINK_EVENT, onLink);
    return () => window.removeEventListener(OPEN_LINK_EVENT, onLink);
  }, [isOwner]);

  return (
    <div className="chat-dock" data-testid="chat-dock" hidden={askTabShowing}>
      <button ref={button} type="button" className="chat-dock-button" data-testid="chat-dock-button"
        aria-label="Ask" aria-expanded={open} title={chatDockTitle(isOwner)} onClick={() => (open ? close() : show())}>
        <MessageCircle size={21} aria-hidden="true" />
      </button>
      {started ? (
        <section ref={panel} className="chat-dock-panel" data-testid="chat-dock-panel" role="dialog" aria-label="Ask" hidden={!open}>
          <BotChat compact pageTicker={pageTicker} pageName={pageName} active={showing} isOwner={isOwner} onClose={close} />
        </section>
      ) : null}
    </div>
  );
}

export default memo(ChatDock);
