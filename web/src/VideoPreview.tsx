import { useEffect, useRef, useState, type ComponentProps } from "react";

// 保留原生播放控件；进入滚动视口附近时才请求首帧和时长。
export function VideoPreview(props: Omit<ComponentProps<"video">, "preload" | "ref">) {
  const ref = useRef<HTMLVideoElement>(null);
  const [nearViewport, setNearViewport] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (nearViewport || !ref.current) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) setNearViewport(true);
    }, { rootMargin: "200px 0px" });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [nearViewport]);

  return <video {...props} ref={ref} preload={nearViewport ? "metadata" : "none"} />;
}
