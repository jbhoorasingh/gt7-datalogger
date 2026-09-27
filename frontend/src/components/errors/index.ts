// Route-level error / empty pages (design handoff §8). Views render these in
// place of their content; App.tsx wires the 404 and the unreachable page.

export { ErrorPage, type ErrorAction, type ErrorPageProps } from "./ErrorPage";
export { NoTelemetryPage } from "./NoTelemetryPage";
export { NotFoundPage, type NotFoundPageProps } from "./NotFoundPage";
export { ServerErrorPage, type ServerErrorPageProps } from "./ServerErrorPage";
export { UnauthorizedPage, type UnauthorizedPageProps } from "./UnauthorizedPage";
export { UnreachablePage } from "./UnreachablePage";
export { useServerReachability, type Reachability } from "./useServerReachability";
