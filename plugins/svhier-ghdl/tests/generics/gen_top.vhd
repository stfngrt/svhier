library ieee; use ieee.std_logic_1164.all;
entity gen_top is generic (N : integer := 3); end entity;
architecture rtl of gen_top is
  component sv_count is generic (COUNT : integer); end component;
  component sv_gated is generic (EN : integer := 1); end component;
  component sv_case is generic (depth : integer); end component;
  component sv_setting is generic (MODE : integer); end component;
  component sv_kinds is generic (
    I : integer; NEG : integer := -4; B : boolean := true; SL : std_logic := '1';
    V : std_logic_vector(3 downto 0) := "1010"; S : string := "hi there"; R : real := 2.5
  ); end component;
  component sv_time is generic (T : time := 5 ns; W : integer := 2); end component;
  component sv_needs_time is generic (T : time := 5 ns); end component;
  component sv_extra is generic (EXTRA : integer := 1); end component;
begin
  u_count : sv_count generic map (COUNT => N);
  u_gated : sv_gated;
  u_case : sv_case generic map (depth => N - 1);
  g_set : for i in 0 to 1 generate
    u_set : sv_setting generic map (MODE => i);
  end generate;
  u_kinds : sv_kinds generic map (I => N * 2);
  u_time : sv_time;
  u_needs : sv_needs_time;
  u_extra : sv_extra;
end architecture;
