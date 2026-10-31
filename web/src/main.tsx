import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./styles/base.css";
import "./styles/screens.css";
import "./styles/run.css";
import "./styles/records.css";
import "./styles/controls.css";
import { App } from "./App";

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 0 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
