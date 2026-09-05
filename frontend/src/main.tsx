import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted rather than fetched from a CDN: one origin, no third-party request, and the
// app still renders correctly with no network.
import "@fontsource-variable/fraunces";
import "@fontsource-variable/literata";
import "@fontsource-variable/inter";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
