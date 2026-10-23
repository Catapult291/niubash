//! `snake`: the classic, niubash-flavored. WASD/arrows steer, p pauses,
//! r (or Enter) restarts after a crash, q quits. The torus playfield has a
//! drawn border so the wrap-around reads as a design choice, and the tick
//! accelerates as the snake grows.

use std::io::{self, Write};
use std::time::{Duration, Instant};

use crossterm::{
    cursor,
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute, terminal,
};

const START_TICK_MS: u64 = 150;
const MIN_TICK_MS: u64 = 60;
const WIDTH: usize = 22;
const HEIGHT: usize = 14;

#[derive(Clone, Copy, PartialEq)]
struct Pos {
    x: usize,
    y: usize,
}

#[derive(Clone, Copy, PartialEq)]
enum Dir {
    Up,
    Down,
    Left,
    Right,
}

#[derive(Clone, Copy, PartialEq)]
enum Phase {
    Running,
    Paused,
    Over,
}

struct Game {
    snake: Vec<Pos>,
    dir: Dir,
    food: Pos,
    score: u32,
    best: u32,
    phase: Phase,
}

impl Game {
    fn new(best: u32) -> Self {
        let mid_x = WIDTH / 2;
        let mid_y = HEIGHT / 2;
        let snake = vec![
            Pos { x: mid_x, y: mid_y },
            Pos {
                x: mid_x - 1,
                y: mid_y,
            },
            Pos {
                x: mid_x - 2,
                y: mid_y,
            },
        ];
        let food = Self::random_pos(&snake);
        Game {
            snake,
            dir: Dir::Right,
            food,
            score: 0,
            best,
            phase: Phase::Running,
        }
    }

    /// The tick shortens as the score climbs — 3 ms per apple, floored.
    fn tick_ms(&self) -> u64 {
        START_TICK_MS
            .saturating_sub((self.score as u64) * 3)
            .max(MIN_TICK_MS)
    }

    fn random_pos(occupied: &[Pos]) -> Pos {
        let mut candidates = Vec::new();
        for y in 0..HEIGHT {
            for x in 0..WIDTH {
                let p = Pos { x, y };
                if !occupied.contains(&p) {
                    candidates.push(p);
                }
            }
        }
        if candidates.is_empty() {
            return Pos { x: 0, y: 0 };
        }
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as usize;
        candidates[nanos % candidates.len()]
    }

    fn tick(&mut self) {
        if self.phase != Phase::Running {
            return;
        }

        let head = self.snake[0];
        // All four edges wrap, so the playfield is a torus in every direction.
        // (Down and Right already wrapped; Up and Left used `wrapping_sub`,
        // which sent the head to `usize::MAX` and made the snake vanish.)
        let new_head = match self.dir {
            Dir::Up => Pos {
                x: head.x,
                y: (head.y + HEIGHT - 1) % HEIGHT,
            },
            Dir::Down => Pos {
                x: head.x,
                y: (head.y + 1) % HEIGHT,
            },
            Dir::Left => Pos {
                x: (head.x + WIDTH - 1) % WIDTH,
                y: head.y,
            },
            Dir::Right => Pos {
                x: (head.x + 1) % WIDTH,
                y: head.y,
            },
        };

        if self.snake.contains(&new_head) {
            self.phase = Phase::Over;
            self.best = self.best.max(self.score);
            return;
        }

        self.snake.insert(0, new_head);

        if new_head == self.food {
            self.score += 1;
            self.food = Self::random_pos(&self.snake);
        } else {
            self.snake.pop();
        }
    }

    fn steer(&mut self, dir: Dir) {
        // A 180° turn into your own neck is refused; queued taps are fine.
        let opposite = matches!(
            (self.dir, dir),
            (Dir::Up, Dir::Down)
                | (Dir::Down, Dir::Up)
                | (Dir::Left, Dir::Right)
                | (Dir::Right, Dir::Left)
        );
        if !opposite {
            self.dir = dir;
        }
    }

    fn draw(&self, stdout: &mut io::Stdout) -> anyhow::Result<()> {
        execute!(stdout, cursor::MoveTo(0, 0))?;
        write!(
            stdout,
            "\x1b[1;96m~ niubash snake ~\x1b[0m  \x1b[1;93mHI {:<5}score {:<5}speed {}%\x1b[0m\x1b[K\n\x1b[K\n",
            if self.best > 0 { self.best.to_string() } else { "-".to_string() },
            self.score,
            (START_TICK_MS * 100 / self.tick_ms())
        )?;

        // Bordered torus: the frame makes the wrap visibly deliberate.
        let hbar: String = "\u{2500}".repeat(WIDTH + 2);
        writeln!(stdout, "  \x1b[90m\u{250c}{hbar}\u{2510}\x1b[0m\x1b[K")?;
        for y in 0..HEIGHT {
            write!(stdout, "  \x1b[90m\u{2502}\x1b[0m ")?;
            for x in 0..WIDTH {
                let pos = Pos { x, y };
                if pos == self.snake[0] {
                    write!(stdout, "\x1b[1;92m@\x1b[0m")?;
                } else if self.snake.contains(&pos) {
                    write!(stdout, "\x1b[92mo\x1b[0m")?;
                } else if pos == self.food {
                    write!(stdout, "\x1b[1;91m*\x1b[0m")?;
                } else {
                    write!(stdout, " ")?;
                }
            }
            writeln!(stdout, " \x1b[90m\u{2502}\x1b[0m\x1b[K")?;
        }
        writeln!(stdout, "  \x1b[90m\u{2514}{hbar}\u{2518}\x1b[0m\x1b[K")?;

        writeln!(stdout, "\x1b[K")?;
        match self.phase {
            Phase::Over => write!(
                stdout,
                "  \x1b[1;91mGAME OVER at {}!\x1b[0m \x1b[90mr restarts, q quits\x1b[0m\x1b[K\n\x1b[K",
                self.score
            ),
            Phase::Paused => write!(
                stdout,
                "  \x1b[1;93mpaused\x1b[0m \x1b[90mp resumes, q quits\x1b[0m\x1b[K\n\x1b[K"
            ),
            Phase::Running => write!(
                stdout,
                "  \x1b[90mwasd/arrows move, p pause, q quit\x1b[0m\x1b[K\n\x1b[K"
            ),
        }?;
        stdout.flush()?;
        Ok(())
    }
}

pub(crate) fn run() -> anyhow::Result<i32> {
    if !crate::terminal::stdout_is_terminal() {
        return Ok(0);
    }
    terminal::enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, terminal::EnterAlternateScreen)?;
    execute!(stdout, cursor::Hide)?;

    let mut best = 0u32;
    let code = 'session: loop {
        let mut game = Game::new(best);
        let mut last_tick = Instant::now();
        loop {
            game.draw(&mut stdout)?;
            if event::poll(Duration::from_millis(10))? {
                if let Event::Key(KeyEvent {
                    code,
                    modifiers,
                    kind,
                    ..
                }) = event::read()?
                {
                    if kind == KeyEventKind::Release {
                        continue;
                    }
                    match code {
                        KeyCode::Char('q') | KeyCode::Char('Q') => break 'session 0,
                        KeyCode::Char('c') if modifiers.contains(KeyModifiers::CONTROL) => {
                            break 'session 0
                        }
                        KeyCode::Char('p') | KeyCode::Char('P') if game.phase != Phase::Over => {
                            game.phase = if game.phase == Phase::Paused {
                                Phase::Running
                            } else {
                                Phase::Paused
                            };
                            last_tick = Instant::now();
                        }
                        KeyCode::Char('r') | KeyCode::Char('R') if game.phase == Phase::Over => {
                            break;
                        }
                        KeyCode::Enter if game.phase == Phase::Over => break,
                        KeyCode::Char('w') | KeyCode::Char('W') | KeyCode::Up => {
                            game.steer(Dir::Up);
                        }
                        KeyCode::Char('s') | KeyCode::Char('S') | KeyCode::Down => {
                            game.steer(Dir::Down);
                        }
                        KeyCode::Char('a') | KeyCode::Char('A') | KeyCode::Left => {
                            game.steer(Dir::Left);
                        }
                        KeyCode::Char('d') | KeyCode::Char('D') | KeyCode::Right => {
                            game.steer(Dir::Right);
                        }
                        _ => {}
                    }
                }
            }
            if last_tick.elapsed() >= Duration::from_millis(game.tick_ms()) {
                game.tick();
                last_tick = Instant::now();
            }
        }
        best = best.max(game.score);
    };

    execute!(stdout, cursor::Show)?;
    execute!(stdout, terminal::LeaveAlternateScreen)?;
    terminal::disable_raw_mode()?;
    Ok(code)
}
