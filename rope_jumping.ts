import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const GROUND_Y = 290;
const PLAYER_X = 140;
const JUMP_VELOCITY = -13.5;
const GRAVITY = 0.62;
const MAX_HEALTH = 100;
const SECOND_ROPE_TIME = 18;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function Rope({ centerX, centerY, radius, angle, speed, color, thickness }) {
  const tipX = centerX + Math.cos(angle) * radius;
  const tipY = centerY + Math.sin(angle) * radius;

  return (
    <g>
      <circle cx={centerX} cy={centerY} r="10" fill="rgba(255,255,255,0.16)" />
      <line
        x1={centerX}
        y1={centerY}
        x2={tipX}
        y2={tipY}
        stroke={color}
        strokeWidth={thickness}
        strokeLinecap="round"
      />
      <circle cx={tipX} cy={tipY} r="8" fill={color} opacity="0.9" />
    </g>
  );
}

function AlienJumper({ y, isJumping, hurtPulse }) {
  const squash = isJumping ? 0.94 : 1;
  const stretch = isJumping ? 1.08 : 1;

  return (
    <motion.g
      animate={{
        y,
        scaleX: squash,
        scaleY: stretch,
      }}
      transition={{ type: "spring", stiffness: 220, damping: 18 }}
    >
      <motion.g animate={{ opacity: hurtPulse ? [1, 0.45, 1] : 1 }} transition={{ duration: 0.28 }}>
        <ellipse cx="0" cy="66" rx="24" ry="14" fill="#0f172a" opacity="0.28" />

        <g transform="translate(0,-8)">
          <rect x="-20" y="22" width="40" height="56" rx="18" fill="#d92626" />
          <rect x="-14" y="28" width="28" height="36" rx="12" fill="#ef4444" opacity="0.7" />

          <rect x="-28" y="30" width="10" height="34" rx="6" fill="#d92626" />
          <rect x="18" y="30" width="10" height="34" rx="6" fill="#d92626" />
          <rect x="-28" y="58" width="10" height="10" rx="5" fill="#fca5a5" opacity="0.45" />
          <rect x="18" y="58" width="10" height="10" rx="5" fill="#fca5a5" opacity="0.45" />

          <rect x="-15" y="74" width="10" height="30" rx="5" fill="#d92626" />
          <rect x="5" y="74" width="10" height="30" rx="5" fill="#d92626" />
          <rect x="-18" y="100" width="14" height="8" rx="4" fill="#991b1b" />
          <rect x="4" y="100" width="14" height="8" rx="4" fill="#991b1b" />

          <ellipse cx="0" cy="8" rx="28" ry="24" fill="#98ff98" />
          <ellipse cx="0" cy="10" rx="22" ry="18" fill="#7df57d" opacity="0.55" />
          <ellipse cx="-9" cy="7" rx="5.5" ry="7.2" fill="#111827" />
          <ellipse cx="9" cy="7" rx="5.5" ry="7.2" fill="#111827" />
          <ellipse cx="-9" cy="7" rx="1.5" ry="2" fill="#ffffff" opacity="0.85" />
          <ellipse cx="9" cy="7" rx="1.5" ry="2" fill="#ffffff" opacity="0.85" />
          <path d="M -7 18 Q 0 22 7 18" stroke="#14532d" strokeWidth="2.2" fill="none" strokeLinecap="round" />
        </g>
      </motion.g>
    </motion.g>
  );
}

export default function JumpRopeGame() {
  const [running, setRunning] = useState(true);
  const [health, setHealth] = useState(MAX_HEALTH);
  const [score, setScore] = useState(0);
  const [best, setBest] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [hurtPulse, setHurtPulse] = useState(false);
  const [gameOver, setGameOver] = useState(false);
  const requestRef = useRef(null);
  const scoreTimer = useRef(0);
  const damageCooldown = useRef(0);

  const stateRef = useRef({
    playerY: 0,
    velocityY: 0,
    rope1Angle: -Math.PI / 2,
    rope2Angle: Math.PI / 2,
    rope1Speed: 0.095,
    rope2Speed: -0.12,
  });

  const secondRopeActive = elapsed >= SECOND_ROPE_TIME;

  const resetGame = () => {
    stateRef.current = {
      playerY: 0,
      velocityY: 0,
      rope1Angle: -Math.PI / 2,
      rope2Angle: Math.PI / 2,
      rope1Speed: 0.095,
      rope2Speed: -0.12,
    };
    setRunning(true);
    setHealth(MAX_HEALTH);
    setScore(0);
    setElapsed(0);
    setGameOver(false);
    setHurtPulse(false);
    scoreTimer.current = 0;
    damageCooldown.current = 0;
  };

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.code === "Space") {
        e.preventDefault();
        if (gameOver) {
          resetGame();
          return;
        }
        const s = stateRef.current;
        const onGround = s.playerY >= 0;
        if (onGround) {
          s.velocityY = JUMP_VELOCITY;
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [gameOver]);

  useEffect(() => {
    let last = performance.now();

    const tick = (now) => {
      const dtMs = now - last;
      last = now;
      const dt = Math.min(dtMs / 16.6667, 2.2);
      const s = stateRef.current;

      if (running && !gameOver) {
        setElapsed((t) => t + dtMs / 1000);

        s.velocityY += GRAVITY * dt;
        s.playerY += s.velocityY * dt;
        if (s.playerY > 0) {
          s.playerY = 0;
          s.velocityY = 0;
        }

        s.rope1Angle += s.rope1Speed * dt;
        if (elapsed >= SECOND_ROPE_TIME) {
          s.rope2Angle += s.rope2Speed * dt;
        }

        scoreTimer.current += dtMs;
        if (scoreTimer.current >= 150) {
          scoreTimer.current = 0;
          setScore((prev) => prev + 1);
        }

        if (damageCooldown.current > 0) {
          damageCooldown.current -= dtMs;
        }

        const playerFeetHeight = -s.playerY;
        const nearGround = playerFeetHeight < 44;
        const rope1Danger = Math.sin(s.rope1Angle) > 0.96 && Math.cos(s.rope1Angle) < -0.1;
        const rope2Danger = secondRopeActive && Math.sin(s.rope2Angle) > 0.96 && Math.cos(s.rope2Angle) < 0.1;

        const shouldDamage = nearGround && damageCooldown.current <= 0 && (rope1Danger || rope2Danger);

        if (shouldDamage) {
          damageCooldown.current = 450;
          setHealth((prev) => {
            const next = clamp(prev - (secondRopeActive ? 14 : 10), 0, MAX_HEALTH);
            if (next <= 0) {
              setGameOver(true);
              setRunning(false);
            }
            return next;
          });
          setHurtPulse(true);
          setTimeout(() => setHurtPulse(false), 220);
        }
      }

      requestRef.current = requestAnimationFrame(tick);
    };

    requestRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(requestRef.current);
  }, [running, gameOver, elapsed, secondRopeActive]);

  useEffect(() => {
    setBest((prev) => Math.max(prev, score));
  }, [score]);

  const s = stateRef.current;
  const jumpHeight = -s.playerY;
  const isJumping = jumpHeight > 8;
  const healthPercent = (health / MAX_HEALTH) * 100;
  const statusText = useMemo(() => {
    if (gameOver) return "Game over — press Space to restart";
    if (!secondRopeActive) return "Press Space to jump the rope";
    return "Two ropes active — stay in rhythm";
  }, [gameOver, secondRopeActive]);

  return (
    <div className="min-h-screen bg-slate-950 text-white p-6 flex items-center justify-center">
      <Card className="w-full max-w-5xl bg-slate-900/95 border-slate-800 shadow-2xl rounded-3xl overflow-hidden">
        <CardContent className="p-5 md:p-7">
          <div className="flex flex-col gap-5">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
              <div>
                <h1 className="text-3xl font-bold tracking-tight">Alien Jump Rope</h1>
                <p className="text-slate-300 mt-1">One-button survival game. Spacebar only.</p>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <div className="px-4 py-2 rounded-2xl bg-slate-800 text-sm">Score: <span className="font-bold">{score}</span></div>
                <div className="px-4 py-2 rounded-2xl bg-slate-800 text-sm">Best: <span className="font-bold">{best}</span></div>
                <div className="px-4 py-2 rounded-2xl bg-slate-800 text-sm">Time: <span className="font-bold">{elapsed.toFixed(1)}s</span></div>
                <Button onClick={resetGame} className="rounded-2xl">Reset</Button>
              </div>
            </div>

            <div className="rounded-3xl bg-slate-800/75 p-4 border border-slate-700">
              <div className="flex items-center justify-between mb-2 text-sm text-slate-300">
                <span>Health</span>
                <span>{health} / {MAX_HEALTH}</span>
              </div>
              <div className="h-5 rounded-full bg-slate-950 overflow-hidden">
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-red-600 via-orange-500 to-lime-400"
                  animate={{ width: `${healthPercent}%` }}
                  transition={{ type: "spring", stiffness: 120, damping: 18 }}
                />
              </div>
            </div>

            <div className="rounded-3xl overflow-hidden border border-slate-800 bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
              <svg viewBox="0 0 900 420" className="w-full h-[500px] max-h-[70vh] block">
                <defs>
                  <linearGradient id="floorGlow" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor="#1e293b" />
                    <stop offset="50%" stopColor="#334155" />
                    <stop offset="100%" stopColor="#1e293b" />
                  </linearGradient>
                </defs>

                <rect x="0" y="0" width="900" height="420" fill="#020617" />
                <circle cx="720" cy="90" r="90" fill="#0f172a" opacity="0.9" />
                <circle cx="720" cy="90" r="52" fill="#1d4ed8" opacity="0.15" />
                <rect x="0" y="330" width="900" height="90" fill="url(#floorGlow)" />
                <rect x="0" y="314" width="900" height="16" fill="#64748b" opacity="0.18" />

                <g transform="translate(650,180)">
                  <Rope
                    centerX={0}
                    centerY={0}
                    radius={165}
                    angle={s.rope1Angle}
                    speed={s.rope1Speed}
                    color="#f59e0b"
                    thickness={8}
                  />
                  {secondRopeActive && (
                    <Rope
                      centerX={0}
                      centerY={0}
                      radius={125}
                      angle={s.rope2Angle}
                      speed={s.rope2Speed}
                      color="#38bdf8"
                      thickness={6}
                    />
                  )}
                </g>

                <g transform={`translate(${PLAYER_X}, ${GROUND_Y + s.playerY})`}>
                  <AlienJumper y={0} isJumping={isJumping} hurtPulse={hurtPulse} />
                </g>

                <g>
                  <text x="28" y="40" fill="#e2e8f0" fontSize="22" fontWeight="700">
                    {statusText}
                  </text>
                  <text x="28" y="74" fill="#94a3b8" fontSize="16">
                    Second rope appears at {SECOND_ROPE_TIME}s
                  </text>
                </g>

                {gameOver && (
                  <g>
                    <rect x="0" y="0" width="900" height="420" fill="#020617" opacity="0.55" />
                    <rect x="250" y="110" width="400" height="180" rx="28" fill="#0f172a" stroke="#334155" />
                    <text x="450" y="170" textAnchor="middle" fill="#f8fafc" fontSize="38" fontWeight="700">
                      Game Over
                    </text>
                    <text x="450" y="210" textAnchor="middle" fill="#cbd5e1" fontSize="20">
                      Final score: {score}
                    </text>
                    <text x="450" y="245" textAnchor="middle" fill="#93c5fd" fontSize="18">
                      Press Space to restart
                    </text>
                  </g>
                )}
              </svg>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
