declare module "vanta/dist/vanta.fog.min" {
  import type { VantaEffect } from "vanta";
  const FOG: VantaEffect;
  export default FOG;
}

declare module "vanta/dist/vanta.waves.min" {
  import type { VantaEffect } from "vanta";
  const WAVES: VantaEffect;
  export default WAVES;
}

declare module "vanta/dist/vanta.net.min" {
  import type { VantaEffect } from "vanta";
  const NET: VantaEffect;
  export default NET;
}

declare module "vanta/dist/vanta.dots.min" {
  import type { VantaEffect } from "vanta";
  const DOTS: VantaEffect;
  export default DOTS;
}

declare module "vanta/dist/vanta.cells.min" {
  import type { VantaEffect } from "vanta";
  const CELLS: VantaEffect;
  export default CELLS;
}

declare module "vanta/dist/vanta.halo.min" {
  import type { VantaEffect } from "vanta";
  const HALO: VantaEffect;
  export default HALO;
}

declare module "vanta/dist/vanta.clouds.min" {
  import type { VantaEffect } from "vanta";
  const CLOUDS: VantaEffect;
  export default CLOUDS;
}

declare module "vanta/dist/vanta.globe.min" {
  import type { VantaEffect } from "vanta";
  const GLOBE: VantaEffect;
  export default GLOBE;
}

declare module "three";

declare module "vanta" {
  import type * as THREE from "three";

  export type VantaInstance = {
    destroy: () => void;
    setOptions: (options: Record<string, unknown>) => void;
    resize: () => void;
  };

  export type VantaOptions = {
    el: HTMLElement | string;
    THREE: typeof THREE;
    mouseControls?: boolean;
    touchControls?: boolean;
    gyroControls?: boolean;
    minHeight?: number;
    minWidth?: number;
    scale?: number;
    scaleMobile?: number;
    color?: number;
    color2?: number;
    backgroundColor?: number;
    size?: number;
    speed?: number;
    [key: string]: unknown;
  };

  export type VantaEffect = (options: VantaOptions) => VantaInstance;
}
