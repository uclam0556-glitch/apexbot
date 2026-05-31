import sys

def refactor():
    with open('main.py', 'r') as f:
        lines = f.readlines()

    start_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("            for symbol in scan_symbols:"):
            start_idx = i
            break

    if start_idx == -1:
        print("Not found")
        return

    # Find end of loop
    end_idx = start_idx + 1
    while end_idx < len(lines):
        line = lines[end_idx]
        if line.strip() and len(line) - len(line.lstrip()) <= 12 and not line.strip().startswith('#'):
            # Check if this is the end. At 12 spaces, it's outside the `for` loop
            if "logger.info(f\"Finished scanning {len(scan_symbols)} coins" in line or "await asyncio.sleep(loop_interval)" in line:
                break
        end_idx += 1

    print(f"Loop is from {start_idx} to {end_idx-1}")
    
    # We will replace start_idx with:
    #             async def process_symbol(symbol):
    #                 async with semaphore:
    
    new_lines = []
    new_lines.extend(lines[:start_idx])
    new_lines.append("            semaphore = asyncio.Semaphore(10)\n")
    new_lines.append("            async def process_symbol(symbol):\n")
    new_lines.append("                async with semaphore:\n")
    
    for i in range(start_idx + 1, end_idx):
        line = lines[i]
        if line.strip() == "continue":
            line = line.replace("continue", "return")
        if line.strip() == "break":
            line = line.replace("break", "return")
            
        # Add 4 spaces indentation
        if line.strip():
            new_lines.append("    " + line)
        else:
            new_lines.append(line)
            
    new_lines.append("\n            # Execute all symbols concurrently\n")
    new_lines.append("            tasks = [process_symbol(sym) for sym in scan_symbols]\n")
    new_lines.append("            await asyncio.gather(*tasks)\n")
    
    new_lines.extend(lines[end_idx:])
    
    with open('main.py', 'w') as f:
        f.writelines(new_lines)
        
    print("Refactored main.py")

refactor()
