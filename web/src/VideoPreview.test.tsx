import { act, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { VideoPreview } from "./VideoPreview";

it("只在视频进入视口附近时预加载，播放期间保留同一播放器", () => {
  type VisibilityCallback = (entries: { isIntersecting: boolean }[]) => void;
  let notify!: VisibilityCallback;
  const disconnect = vi.fn();
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: VisibilityCallback) { notify = callback; }
    observe() {}
    disconnect = disconnect;
  });
  try {
    const view = render(<VideoPreview src="/clip.mp4" controls />);
    const video = view.container.querySelector("video")!;
    expect(video.preload).toBe("none");
    const intersect = (isIntersecting: boolean) => act(() => {
      notify([{ isIntersecting }]);
    });
    intersect(false);
    expect(video.preload).toBe("none");
    intersect(true);
    expect(video.preload).toBe("metadata");
    expect(disconnect).toHaveBeenCalledOnce();
    expect(view.container.querySelector("video")).toBe(video);
  } finally {
    vi.unstubAllGlobals();
  }
});
