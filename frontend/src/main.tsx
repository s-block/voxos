import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { ErrorBoundary } from "@sentry/react";
import "@livekit/components-styles";
import "./styles.css";
import { App } from "./App";
import { initializeMonitoring } from "./monitoring";

initializeMonitoring();

const root = document.getElementById("root");

if (!root) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <ErrorBoundary fallback={<p>VoxOS encountered an error. Reload the page to try again.</p>}>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </ErrorBoundary>,
);
