export const SPORTS = [
  "Ride",
  "VirtualRide",
  "GravelRide",
  "MountainBikeRide",
  "EBikeRide",
  "Run",
  "TrailRun",
  "Hike",
  "Walk",
  "Swim",
  "Workout",
];

export function sportOptions(current?: string | null): string[] {
  if (current && !SPORTS.includes(current)) {
    return [current, ...SPORTS];
  }
  return SPORTS;
}
