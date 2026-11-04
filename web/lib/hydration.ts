"use client";

import { useSyncExternalStore } from "react";

/** Nothing ever changes, so the subscription is a no-op. */
const subscribe = () => () => {};

const getSnapshot = () => true;

/**
 * False during server rendering and the first client render.
 *
 * A submit button must stay disabled until React has attached the submit
 * handler. Otherwise a click before hydration falls through to the browser's
 * native form submission, and for a GET form the password ends up in the URL.
 * `useSyncExternalStore` gives the server and the first client render the same
 * `false`, so hydration does not mismatch.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
