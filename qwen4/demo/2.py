def init_board(row: int, col: int) -> list[list[str]]:
  return [['-' for _ in range(col)] for _ in range(row)]

def draw_board(board: list[list[str]]) -> None:
    row = len(board)
    col = len(board[0]) if row > 0 else 0
    print("  " + " ".join(str(i) for i in range(col)))
    for i in range(row):
        print(f"{i} " + " ".join(board[i]))
    print("-" * 20)

def player_move(board: list[list[str]], player: str) -> tuple[int, int]:
    row = len(board)
    col = len(board[0]) if row > 0 else 0 
    while True:
        try:
            input_str = input(f"玩家 {player} 落子（输入格式：行 列，如 3 4):")
            x, y = map(int, input_str.strip().split())
            if 0 <= x < row and 0 <= y < col:
                if board[x][y] == '-':
                    board[x][y] = player 
                    return x, y
                else:
                    print("该位置已被占用！请重新选择")
            else:
                print(f"坐标超出范围（有效范围：行 0~{row-1}，列 0~{col-1}）！请重新输入")
        except ValueError:
            print("输入格式错误！请输入两个整数（如 3 4)")

def check_win(board: list[list[str]], x: int, y: int, player: str) -> bool:
    row = len(board)
    col = len(board[0]) if row > 0 else 0
    directions = [
        (0, 1),  
        (1, 0),  
        (1, 1),  
        (1, -1) 
    ]
    
    for dx, dy in directions:
        count = 1 ;
        for step in range(1, 5):
            nx = x + dx * step
            ny = y + dy * step
            if 0 <= nx < row and 0 <= ny < col and board[nx][ny] == player:
                count += 1
            else:
                break
        for step in range(1, 5):
            nx = x - dx * step
            ny = y - dy * step
            if 0 <= nx < row and 0 <= ny < col and board[nx][ny] == player:
                count += 1
            else:
                break
        if count >= 5:
            return True
    return False

def is_board_full(board: list[list[str]]) -> bool:
    for row in board:
        if '-' in row:
            return False
    return True

def gobang_game(row: int = 15, col: int = 15) -> None:
    print("=" * 30)
    print("五子棋游戏")
    print("规则：玩家 X 先行，率先连成五子者获胜")
    print("=" * 30)
    board = init_board(row, col)
    current_player = 'X'  
    
    while True:
        draw_board(board)
        x, y = player_move(board, current_player)
        if check_win(board, x, y, current_player):
            draw_board(board)
            print(f"\n🎉 玩家 {current_player} 获胜！游戏结束！")
            break
        if is_board_full(board):
            draw_board(board)
            print("\n🤝 棋盘已满，平局！游戏结束！")
            break
        current_player = 'O' if current_player == 'X' else 'X'
if __name__ == "__main__":
    gobang_game(row=10, col=10)  